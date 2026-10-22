"""Syllabus extraction orchestration.

Pipeline: validate and read the document -> optional LLM interpretation ->
validate and post-process -> deterministic rule-based fallback. The result is
never trusted blindly: durations are clamped, prerequisite references are
resolved, cycles are broken with a warning, and low-confidence items are
flagged for the professor to review and correct.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.ai.llm import SyllabusInterpreter
from app.ai.rule_based import RuleBasedInterpreter, detect_injection_attempts, reference_parts
from app.ai.schemas import SyllabusExtraction
from app.core.config import Settings
from app.core.errors import ValidationFailedError

logger = logging.getLogger(__name__)

# Matches the API request schema for the refine endpoint (schemas/syllabus.py);
# enforced here too so the service cannot be used as an unbounded-prompt sink.
MAX_REFINE_PROMPT_CHARS = 4_000


@dataclass
class ExtractionOutcome:
    extraction: SyllabusExtraction
    provider: str
    used_llm: bool
    warnings: list[str] = field(default_factory=list)


def _clamp_durations(extraction: SyllabusExtraction, settings: Settings) -> None:
    for unit in extraction.units:
        for topic in unit.topics:
            minutes = max(
                settings.min_topic_minutes,
                min(settings.max_topic_minutes, topic.estimated_duration_minutes),
            )
            rounded = int(round(minutes / 15.0) * 15) or settings.min_topic_minutes
            # Re-clamp: rounding to the 15-minute grid can overshoot a maximum that
            # is not itself a multiple of 15.
            topic.estimated_duration_minutes = max(
                settings.min_topic_minutes, min(settings.max_topic_minutes, rounded)
            )


def _normalize_title(title: str) -> str:
    return " ".join(title.split()).strip().lower()


def _resolve_prerequisites(extraction: SyllabusExtraction) -> list[str]:
    """Resolve prerequisite references to topic keys.

    Documents state prerequisites by name ("Prerequisite: Arrays and Lists"), so
    a reference that is not already a topic key is matched against topic titles.
    A reference naming several topics ("paging and segmentation") is split only
    when the whole text does not match a topic title. Unresolvable references are
    dropped and flagged for professor review instead of being carried into the
    schedule as unknown edges.
    """

    warnings: list[str] = []
    topics = [topic for unit in extraction.units for topic in unit.topics]
    known_keys = {topic.key for topic in topics}
    key_by_title = {_normalize_title(topic.title): topic.key for topic in topics}

    def lookup(reference: str) -> str | None:
        if reference in known_keys:
            return reference
        return key_by_title.get(_normalize_title(reference))

    for topic in topics:
        resolved: list[str] = []
        unknown: list[str] = []
        for reference in topic.prerequisite_keys:
            keys: list[str] = []
            direct = lookup(reference)
            if direct is not None:
                keys = [direct]
            else:
                parts = reference_parts(reference)
                parts_resolved = [lookup(part) for part in parts]
                if len(parts) > 1 and all(part is not None for part in parts_resolved):
                    keys = [part for part in parts_resolved if part is not None]
                else:
                    unknown.append(reference)
            for key in keys:
                if key != topic.key and key not in resolved:
                    resolved.append(key)
        if unknown:
            warnings.append(
                f"Topic '{topic.title}' referenced unknown prerequisite key(s): "
                + ", ".join(unknown)
            )
            topic.needs_review = True
        topic.prerequisite_keys = resolved
    return warnings


def _break_cycles(extraction: SyllabusExtraction) -> list[str]:
    """Remove prerequisite edges involved in cycles rather than rejecting the document."""

    topics = {topic.key: topic for unit in extraction.units for topic in unit.topics}
    indegree = {key: 0 for key in topics}
    for topic in topics.values():
        for prerequisite in topic.prerequisite_keys:
            if prerequisite in topics:
                indegree[topic.key] += 1

    queue = [key for key, degree in indegree.items() if degree == 0]
    resolved: set[str] = set()
    while queue:
        key = queue.pop()
        resolved.add(key)
        for topic in topics.values():
            if key in topic.prerequisite_keys:
                indegree[topic.key] -= 1
                if indegree[topic.key] == 0:
                    queue.append(topic.key)

    unprocessed = set(topics) - resolved
    if not unprocessed:
        return []

    warnings = []
    # Sorted so the same document always produces the same warning order.
    for key in sorted(unprocessed):
        topic = topics[key]
        removed = [p for p in topic.prerequisite_keys if p in unprocessed]
        if removed:
            topic.prerequisite_keys = [p for p in topic.prerequisite_keys if p not in unprocessed]
            topic.needs_review = True
            warnings.append(
                f"A dependency cycle involving '{topic.title}' was removed; please confirm the "
                "intended prerequisite order."
            )
    return warnings


class SyllabusExtractionService:
    def __init__(self, settings: Settings, llm: SyllabusInterpreter | None = None) -> None:
        self._settings = settings
        self._llm = llm
        self._fallback = RuleBasedInterpreter()

    def extract(self, text: str) -> ExtractionOutcome:
        warnings: list[str] = []
        extraction: SyllabusExtraction | None = None
        used_llm = False

        if self._llm is not None:
            try:
                extraction = self._llm.extract(text)
                used_llm = True
                # Provider-authored prose and labels are untrusted: a successful
                # LLM call must not be able to inject text into the teacher's UI.
                extraction.warnings = []
                extraction.provider = self._llm.name
                warnings.append(
                    f"Syllabus structure interpreted with the '{self._llm.name}' provider."
                )
            except Exception as exc:
                # The professor must always get a usable preview: a provider outage,
                # a timeout, or an invalid payload degrades to rule-based extraction.
                logger.warning("LLM extraction failed, falling back to rule-based: %s", exc)
                warnings.append(
                    "AI interpretation was unavailable, so the deterministic rule-based "
                    "extractor was used instead. Review the extracted structure carefully."
                )

        if extraction is None:
            extraction = self._fallback.extract(text)
            if self._llm is None:
                warnings.append(
                    "No LLM provider is configured; the deterministic rule-based extractor "
                    "was used."
                )

        warnings.extend(_resolve_prerequisites(extraction))
        warnings.extend(_break_cycles(extraction))
        _clamp_durations(extraction, self._settings)
        extraction.warnings = list(extraction.warnings) + warnings

        injection_findings = detect_injection_attempts(text)
        if injection_findings:
            extraction.warnings.append(
                "The document contains instruction-like text. It was treated as data only and "
                "could not affect system behaviour."
            )

        return ExtractionOutcome(
            extraction=extraction,
            provider=extraction.provider,
            used_llm=used_llm,
            warnings=extraction.warnings,
        )

    def refine(self, current: SyllabusExtraction, prompt: str) -> ExtractionOutcome:
        """Modify an existing extraction structure according to the professor's prompt."""

        if self._llm is None:
            raise ValidationFailedError(
                "No AI provider is configured. Configure a provider in Settings "
                "(or set OPENROUTER_API_KEY, GEMINI_API_KEY, or OLLAMA in backend/.env) "
                "to use prompt-based syllabus editing.",
                code="AI_NOT_CONFIGURED",
            )

        instruction = (prompt or "").strip()
        if not instruction:
            raise ValidationFailedError(
                "Describe the change you want before asking AI to refine the syllabus.",
                code="INVALID_PROMPT",
            )
        if len(instruction) > MAX_REFINE_PROMPT_CHARS:
            raise ValidationFailedError(
                f"The refinement prompt exceeds the {MAX_REFINE_PROMPT_CHARS}-character limit.",
                code="INVALID_PROMPT",
            )

        try:
            extraction = self._llm.refine(current, instruction)
        except Exception as exc:
            logger.warning("LLM prompt refinement failed: %s", exc)
            raise ValidationFailedError(
                f"AI refinement failed: {exc}. Your current syllabus preview was preserved.",
                code="AI_REFINEMENT_FAILED",
            ) from exc

        # Same containment as extraction: the model does not get to write the
        # warnings the professor reads.
        extraction.warnings = []
        extraction.provider = self._llm.name
        warnings: list[str] = [
            f"Syllabus structure refined using the '{self._llm.name}' provider."
        ]
        warnings.extend(_resolve_prerequisites(extraction))
        warnings.extend(_break_cycles(extraction))
        _clamp_durations(extraction, self._settings)
        extraction.warnings = list(extraction.warnings) + warnings

        return ExtractionOutcome(
            extraction=extraction,
            provider=self._llm.name,
            used_llm=True,
            warnings=extraction.warnings,
        )

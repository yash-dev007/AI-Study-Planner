"""Deterministic rule-based syllabus interpretation.

This provider is always available and needs no network access or API key. It
recognises common academic syllabus layouts (units/chapters/modules, bullet or
numbered topic lists, learning outcomes) and returns a validated
``SyllabusExtraction``. Low-confidence output is flagged for professor review
instead of being silently trusted.
"""

from __future__ import annotations

import re
from itertools import count
from typing import TypedDict

from app.ai.rules import classify_difficulty, detection_confidence, estimate_duration_minutes
from app.ai.schemas import Difficulty, ExtractedTopic, ExtractedUnit, SyllabusExtraction
from app.core.config import settings
from app.core.errors import DocumentProcessingError

UNIT_PATTERN = re.compile(
    r"^\s*(?:unit|module|chapter|part)\s*[-–—:.]?\s*([ivxlcdm]+|\d+)\s*[-–—:.]?\s*(.*)$",
    re.IGNORECASE,
)
ROMAN_PATTERN = re.compile(r"^\s*([IVXLC]{2,6})\.?\s+(.{3,})$")
# A topic heading: "Topic 1.1: Arrays and Lists", "Lecture 4 - Scheduling".
# The leading label and number are structure, not part of the topic title.
TOPIC_PATTERN = re.compile(
    r"^\s*(?:topics?|lessons?|lectures?)\s*[-–—:.]?\s*(\d+(?:\.\d+)*)\s*[-–—:.]?\s*(.{3,})$",
    re.IGNORECASE,
)
BULLET_PATTERN = re.compile(r"^\s*[-•*·▪◦o]\s+(.+)$")
NUMBERED_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]?\s+(.+)$")
LETTERED_PATTERN = re.compile(r"^\s*([a-h])[.)]\s+(.+)$")
OBJECTIVE_PATTERN = re.compile(
    r"^\s*(?:co\s*\d+|course\s+outcome|learning\s+outcome|objective)s?\s*[:\-–]?\s*(.+)$",
    re.IGNORECASE,
)
PAGE_NUMBER_PATTERN = re.compile(r"^\s*(?:page\s*)?\d+\s*(?:of\s*\d+)?\s*$", re.IGNORECASE)
# A bare section label such as "Topics:" or "Contents:" - a label, not content.
SECTION_LABEL_PATTERN = re.compile(
    r"^\s*(?:course\s+)?(?:topics?|contents?|units?|modules?|chapters?|parts?|weeks?)\s*"
    r"[:\-–—]?\s*$",
    re.IGNORECASE,
)
# Course metadata such as "Evaluation: ..." or "Textbook: ..." - not teaching topics.
METADATA_PATTERN = re.compile(
    r"^\s*(?:course\s+)?(?:evaluation|assessment|grading|marks|weightage|references?|"
    r"text\s*books?|textbooks?|readings?|prerequisites?|instructor|office\s+hours|attendance|"
    r"polic(?:y|ies)|contact|email|credits?|semester|department|schedule|calendar|"
    r"syllabus\s+version)\b\s*[:\-–—]",
    re.IGNORECASE,
)
# Durations written into the syllabus, e.g. "(90 minutes)", "2 hours", "45 min".
DURATION_HINT_PATTERN = re.compile(
    r"[\(\[]?\s*(\d{1,3})\s*(min(?:ute)?s?|hrs?|hours?)\b\s*[\)\]]?",
    re.IGNORECASE,
)
# Properties of the topic they appear under, written as list items:
#   - Duration: 60 minutes
#   - Prerequisite: Arrays and Lists
#   - Difficulty: hard
# They describe the topic above them and must never become topics themselves.
TOPIC_ATTRIBUTE_PATTERN = re.compile(
    r"^(?P<label>duration|estimated\s+(?:duration|time)|teaching\s+hours|"
    r"difficulty|level|"
    r"prerequisites?|prereqs?|requires|builds\s+on|"
    r"learning\s+outcomes?|outcomes?|objectives?|"
    r"sub-?topics?|topics\s+covered|references?|text\s*books?|textbooks?|resources?|notes?)"
    r"\s*[:\-–—]\s*(?P<value>.+)$",
    re.IGNORECASE,
)
DURATION_LABELS = frozenset({"duration", "estimated duration", "estimated time", "teaching hours"})
DIFFICULTY_LABELS = frozenset({"difficulty", "level"})
PREREQUISITE_LABELS = frozenset(
    {"prerequisite", "prerequisites", "prereq", "prereqs", "requires", "builds on"}
)
OBJECTIVE_LABELS = frozenset(
    {"objective", "objectives", "outcome", "outcomes", "learning outcome", "learning outcomes"}
)
DIFFICULTY_WORDS: dict[str, Difficulty] = {
    "easy": "easy",
    "basic": "easy",
    "beginner": "easy",
    "introductory": "easy",
    "low": "easy",
    "medium": "medium",
    "moderate": "medium",
    "intermediate": "medium",
    "standard": "medium",
    "hard": "hard",
    "difficult": "hard",
    "advanced": "hard",
    "challenging": "hard",
    "complex": "hard",
    "high": "hard",
}
_HOURS_VALUE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:hrs?|hours?)\b", re.IGNORECASE)
_MINUTES_VALUE = re.compile(r"(\d{1,4})\s*(?:min(?:ute)?s?)\b", re.IGNORECASE)
_BARE_MINUTES = re.compile(r"^(\d{1,4})(?:\.0+)?$")
# Hard separators only. "and" is resolved later so a topic *named* "Arrays and
# Lists" is not torn into two references.
_REFERENCE_SPLIT = re.compile(r"\s*(?:,|;|\+|&)\s*")
_CONJUNCTION_SPLIT = re.compile(r"\s+and\s+", re.IGNORECASE)
INJECTION_PATTERNS = (
    re.compile(r"ignore (?:all )?(?:previous|prior|above) instructions", re.IGNORECASE),
    re.compile(r"disregard (?:the )?(?:system|previous) prompt", re.IGNORECASE),
    re.compile(r"you are now|act as (?:an?|the) (?:system|assistant)", re.IGNORECASE),
    re.compile(r"reveal (?:your )?(?:system )?prompt", re.IGNORECASE),
)


def detect_injection_attempts(text: str) -> list[str]:
    findings: list[str] = []
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            findings.append(pattern.pattern)
    return findings


def _clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip(" .:-–—\t")
    return title[:300]


def _duration_hint(text: str) -> int | None:
    """Minutes stated in the text itself, e.g. "(90 minutes)" or "2 hours"."""

    match = DURATION_HINT_PATTERN.search(text)
    if match is None:
        return None
    value = int(match.group(1))
    unit = (match.group(2) or "min").lower()
    minutes = value * 60 if unit.startswith(("hr", "hour")) else value
    if minutes < settings.min_topic_minutes or minutes > settings.max_topic_minutes:
        return None
    return minutes


def _topic_text(title: str) -> str:
    """The topic title without its duration hint."""

    cleaned = _clean_title(DURATION_HINT_PATTERN.sub(" ", title))
    return cleaned or _clean_title(title)


def _labeled_duration(value: str) -> int | None:
    """Minutes from a ``Duration:`` value: "60", "60 minutes", or "1.5 hours"."""

    for pattern, factor in ((_HOURS_VALUE, 60), (_MINUTES_VALUE, 1), (_BARE_MINUTES, 1)):
        match = pattern.search(value)
        if match is None:
            continue
        minutes = round(float(match.group(1)) * factor)
        if settings.min_topic_minutes <= minutes <= settings.max_topic_minutes:
            return minutes
        return None
    return None


def _reference_names(value: str) -> list[str]:
    """Split "Arrays, Stacks and Recursion" into individual topic references."""

    names: list[str] = []
    for part in _REFERENCE_SPLIT.split(value):
        name = _clean_title(re.sub(r"\([^)]*\)", "", part))
        if len(name) >= 2 and name not in names:
            names.append(name)
    return names


def reference_parts(reference: str) -> list[str]:
    """The individual names in a prerequisite phrased with "and"."""

    return [part.strip() for part in _CONJUNCTION_SPLIT.split(reference) if part.strip()]


def _list_item_content(line: str) -> str | None:
    """The content of a bulleted, numbered, or lettered list item."""

    bullet = BULLET_PATTERN.match(line)
    if bullet is not None:
        return bullet.group(1)
    numbered = NUMBERED_PATTERN.match(line)
    if numbered is not None and "." not in numbered.group(1):
        return numbered.group(2)
    lettered = LETTERED_PATTERN.match(line)
    if lettered is not None:
        return lettered.group(2)
    return None


class UnitAccumulator(TypedDict):
    """A unit while it is still being parsed from the document."""

    key: str
    title: str
    description: str
    topics: list[ExtractedTopic]
    unit_detected: bool


class RuleBasedInterpreter:
    name = "rule_based"

    def extract(self, text: str) -> SyllabusExtraction:
        lines = [line for line in text.split("\n")]
        filtered = [line for line in lines if line.strip() and not PAGE_NUMBER_PATTERN.match(line)]

        units: list[ExtractedUnit] = []
        accumulated: list[UnitAccumulator] = []
        warnings: list[str] = []
        preamble: list[str] = []
        metadata: list[str] = []
        current_unit: UnitAccumulator | None = None
        current_topic: ExtractedTopic | None = None
        topic_keys = count(1)
        unit_keys = count(1)

        def start_unit(title: str) -> None:
            nonlocal current_unit, current_topic
            current_unit = {
                "key": f"u{next(unit_keys)}",
                "title": _clean_title(title) or f"Unit {len(accumulated) + 1}",
                "description": "",
                "topics": [],
                "unit_detected": bool(title),
            }
            accumulated.append(current_unit)
            current_topic = None

        def add_topic(title: str, *, style: str, description: str = "") -> None:
            nonlocal current_topic
            if current_unit is None:
                start_unit("")
            hint = _duration_hint(title)
            title = _topic_text(title)
            if not title:
                return
            unit_detected = bool(current_unit and current_unit["unit_detected"])
            confidence = detection_confidence(unit_detected=unit_detected, bullet_style=style)
            difficulty = classify_difficulty(title, description)
            duration = hint or estimate_duration_minutes(title, description, difficulty)
            topic = ExtractedTopic(
                key=f"t{next(topic_keys)}",
                title=title,
                description=description[:4000],
                estimated_duration_minutes=duration,
                difficulty=difficulty,
                confidence=confidence,
                needs_review=confidence < 0.65,
            )
            assert current_unit is not None
            current_unit["topics"].append(topic)
            current_topic = topic

        def append_detail(value: str) -> None:
            nonlocal current_topic
            value = _clean_title(value)
            if not value:
                return
            if current_topic is None:
                add_topic(value, style="indented")
                return
            if OBJECTIVE_PATTERN.match(value):
                current_topic.learning_objectives.append(value)
                return
            if len(current_topic.description) < 3500:
                current_topic.description = (current_topic.description + " " + value).strip()

        def add_objective(value: str) -> None:
            """Route an outcome line to the current topic, else to the unit."""

            value = _clean_title(value)
            if not value:
                return
            if current_topic is not None:
                current_topic.learning_objectives.append(value)
                return
            if current_unit is not None:
                if len(current_unit["description"]) < 3500:
                    current_unit["description"] = (
                        current_unit["description"] + " " + value
                    ).strip()
                return
            preamble.append(value)

        def apply_topic_attribute(content: str) -> bool:
            """Attach a `Duration:`/`Prerequisite:` list item to the topic above it.

            Returns True when the line was consumed as a property of the open
            topic. A property with no topic to attach to yet (for example a
            "Duration:" bullet before any teaching content) is kept out of the
            topic list rather than invented as a topic.
            """

            match = TOPIC_ATTRIBUTE_PATTERN.match(_clean_title(content))
            if match is None:
                return False
            if current_topic is None:
                metadata.append(_clean_title(content))
                return True

            label = " ".join(match.group("label").lower().split())
            value = _clean_title(match.group("value"))
            if label in DURATION_LABELS:
                minutes = _labeled_duration(value)
                if minutes is None:
                    append_detail(f"{label.title()}: {value}")
                else:
                    current_topic.estimated_duration_minutes = minutes
                return True
            if label in PREREQUISITE_LABELS:
                references = _reference_names(value)
                if not references:
                    append_detail(f"{label.title()}: {value}")
                    return True
                known = list(current_topic.prerequisite_keys)
                current_topic.prerequisite_keys = known + [
                    item for item in references if item not in known
                ]
                return True
            if label in OBJECTIVE_LABELS:
                add_objective(value)
                return True
            if label in DIFFICULTY_LABELS:
                difficulty = DIFFICULTY_WORDS.get(value.strip(" .:").lower())
                if difficulty is None:
                    append_detail(f"{label.title()}: {value}")
                else:
                    current_topic.difficulty = difficulty
                return True
            # Subtopics, references, and notes stay as teaching detail.
            append_detail(f"{label.title()}: {value}")
            return True

        if not filtered:
            raise DocumentProcessingError(
                "The document does not contain any readable text.",
                code="NO_TEXT_EXTRACTED",
            )

        for raw_line in filtered:
            line = raw_line.rstrip()
            stripped = line.strip()

            unit_match = UNIT_PATTERN.match(stripped)
            if unit_match and len(stripped) < 160:
                title = unit_match.group(2) or f"Unit {unit_match.group(1)}"
                start_unit(title)
                continue

            roman_match = ROMAN_PATTERN.match(stripped)
            if roman_match and len(stripped) < 120 and stripped.isupper():
                start_unit(roman_match.group(2))
                continue

            topic_match = TOPIC_PATTERN.match(stripped)
            if topic_match and len(stripped) < 200:
                add_topic(topic_match.group(2), style="numbered")
                continue

            if OBJECTIVE_PATTERN.match(stripped):
                add_objective(stripped)
                continue

            if SECTION_LABEL_PATTERN.match(stripped):
                # "Topics:" style labels introduce the following list.
                current_topic = None
                continue

            # A list item naming a property of the topic above it ("- Duration:
            # 60", "- Prerequisite: Arrays") describes that topic. This is
            # checked before the course-metadata filter so a prerequisite next
            # to its topic is kept instead of being dropped as course prose.
            item_content = _list_item_content(stripped)
            if item_content is not None and apply_topic_attribute(item_content):
                continue

            if METADATA_PATTERN.match(stripped):
                # Grading, textbook, and policy lines are course information.
                metadata.append(stripped)
                current_topic = None
                continue

            bullet_match = BULLET_PATTERN.match(stripped)
            if bullet_match:
                add_topic(bullet_match.group(1), style="bullet")
                continue

            numbered_match = NUMBERED_PATTERN.match(stripped)
            if numbered_match:
                number, content = numbered_match.groups()
                depth = number.count(".")
                if depth == 0 and (len(stripped) < 160 or current_unit is None):
                    add_topic(content, style="numbered")
                else:
                    append_detail(content)
                continue

            lettered_match = LETTERED_PATTERN.match(stripped)
            if lettered_match:
                append_detail(lettered_match.group(2))
                continue

            if stripped.isupper() and 3 <= len(stripped) <= 120 and not current_unit:
                start_unit(stripped.title())
                continue

            # The same for an unbulleted property line such as "Duration: 60".
            if apply_topic_attribute(stripped):
                continue

            # A short line following a topic is treated as detail; otherwise it
            # becomes a topic only when no structured topics have been found.
            if current_topic is not None and len(stripped) > 40:
                append_detail(stripped)
            elif current_unit is None:
                # Preamble before the first unit heading (course title, semester,
                # instructor, evaluation notes). It is course information, not
                # teaching content, so it only becomes a topic when the line
                # itself states how long it takes.
                if _duration_hint(stripped) is not None:
                    start_unit("Syllabus")
                    add_topic(stripped, style="indented")
                else:
                    preamble.append(stripped)
            else:
                add_topic(stripped, style="indented")

        units = [
            ExtractedUnit(
                key=item["key"],
                title=item["title"],
                description=item["description"],
                sequence_number=index,
                topics=item["topics"],
            )
            for index, item in enumerate([item for item in accumulated if item["topics"]], start=1)
        ]
        if not units:
            # Last-resort deterministic segmentation: every substantial line is a
            # topic under a single unit, flagged for review.
            fallback_topics = []
            for index, line in enumerate(filtered[:60], start=1):
                title = _topic_text(line)
                if len(title) < 4:
                    continue
                fallback_topics.append(
                    ExtractedTopic(
                        key=f"t{index}",
                        title=title,
                        estimated_duration_minutes=(
                            _duration_hint(line) or estimate_duration_minutes(title)
                        ),
                        difficulty=classify_difficulty(title),
                        confidence=0.4,
                        needs_review=True,
                    )
                )
            if not fallback_topics:
                raise DocumentProcessingError(
                    "No syllabus topics could be identified in the document. Check the file or "
                    "add the structure manually.",
                    code="NO_TOPICS_DETECTED",
                )
            units = [
                ExtractedUnit(
                    key="u1",
                    title="Syllabus",
                    sequence_number=1,
                    topics=fallback_topics,
                )
            ]
            warnings.append(
                "No unit or topic structure was detected. Every substantial line was treated "
                "as a topic and flagged for review."
            )
        elif preamble:
            warnings.append(
                f"{len(preamble)} line(s) before the first unit heading were read as course "
                "information rather than teaching topics."
            )
        if metadata:
            warnings.append(
                f"{len(metadata)} course metadata line(s) (grading, textbooks, policies) were "
                "kept out of the topic list. Add them manually if they need to be taught."
            )

        low_confidence = sum(1 for unit in units for topic in unit.topics if topic.needs_review)
        if low_confidence:
            warnings.append(
                f"{low_confidence} topic(s) were extracted with low confidence and need review."
            )

        injections = detect_injection_attempts(text)
        if injections:
            warnings.append(
                "The document contains text that resembles instructions to an AI system. "
                "It was treated strictly as syllabus content and ignored for control purposes."
            )

        average_confidence = round(
            sum(topic.confidence for unit in units for topic in unit.topics)
            / max(1, sum(len(unit.topics) for unit in units)),
            2,
        )
        return SyllabusExtraction(
            units=units,
            warnings=warnings,
            provider=self.name,
            confidence=average_confidence,
        )

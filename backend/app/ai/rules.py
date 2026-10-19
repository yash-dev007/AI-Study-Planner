"""Deterministic duration and difficulty estimation rules.

These rules are intentionally transparent and configurable; they are the
baseline used when no LLM provider is configured and the fallback when an LLM
call fails. They never invent syllabus content - they only estimate duration,
difficulty, and review flags for content that was already extracted.
"""

from __future__ import annotations

import re

from app.ai.schemas import Difficulty
from app.core.config import settings

_EASY_KEYWORDS = (
    "introduction",
    "overview",
    "basic",
    "fundamental",
    "history",
    "definition",
    "terminology",
    "concept",
)
_HARD_KEYWORDS = (
    "advanced",
    "complex",
    "optimization",
    "optimisation",
    "proof",
    "derivation",
    "implementation",
    "project",
    "case study",
    "security",
    "distributed",
    "concurrency",
    "machine learning",
    "compiler",
)
_MEDIUM_KEYWORDS = ("analysis", "design", "application", "algorithm", "model", "system")
_DURATION_BONUS = {"easy": 0, "medium": 15, "hard": 30}


def classify_difficulty(title: str, description: str = "") -> Difficulty:
    text = f"{title} {description}".lower()
    if any(keyword in text for keyword in _HARD_KEYWORDS):
        return "hard"
    if any(keyword in text for keyword in _EASY_KEYWORDS):
        return "easy"
    if any(keyword in text for keyword in _MEDIUM_KEYWORDS):
        return "medium"
    return "medium"


def estimate_duration_minutes(
    title: str,
    description: str = "",
    difficulty: str | None = None,
    *,
    has_explicit_duration: bool = False,
) -> int:
    """Estimate teaching minutes, rounded to a 15-minute grid."""

    difficulty = difficulty or classify_difficulty(title, description)
    text = f"{title} {description}".lower()
    minutes = settings.default_topic_minutes + _DURATION_BONUS.get(difficulty, 0)
    if re.search(r"\b(lab|laboratory|practical|workshop)\b", text):
        minutes += 30
    if re.search(r"\b(proof|derivation|numerical|simulation)\b", text):
        minutes += 15
    word_count = len(text.split())
    if word_count > 18:
        minutes += 15
    if " and " in text or "," in text:
        minutes += 10
    if has_explicit_duration:
        minutes = settings.default_topic_minutes
    minutes = max(settings.min_topic_minutes, min(settings.max_topic_minutes, minutes))
    return int(round(minutes / 15.0) * 15) or settings.min_topic_minutes


def detection_confidence(*, unit_detected: bool, bullet_style: str) -> float:
    confidence = 0.5
    if unit_detected:
        confidence += 0.25
    if bullet_style in {"bullet", "numbered"}:
        confidence += 0.15
    elif bullet_style == "indented":
        confidence += 0.05
    return round(min(confidence, 0.95), 2)

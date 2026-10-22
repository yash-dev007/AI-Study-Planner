"""Provider-independent syllabus extraction contracts.

LLM output is parsed into these Pydantic models before it reaches any other
part of the system. An invalid payload is rejected and the caller falls back
to the deterministic rule-based extraction.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Difficulty = Literal["easy", "medium", "hard"]


class ExtractedTopic(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    learning_objectives: list[str] = Field(default_factory=list, max_length=20)
    estimated_duration_minutes: int = Field(default=60, ge=5, le=600)
    difficulty: Difficulty = "medium"
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    needs_review: bool = False
    prerequisite_keys: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("title", "description", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return " ".join(value.split()).strip()
        return value

    @field_validator("learning_objectives", "prerequisite_keys", mode="before")
    @classmethod
    def clean_lists(cls, value: object) -> object:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []


class ExtractedUnit(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    sequence_number: int = Field(ge=1)
    topics: list[ExtractedTopic] = Field(default_factory=list, min_length=1)

    @field_validator("title", "description", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return " ".join(value.split()).strip()
        return value


class SyllabusExtraction(BaseModel):
    units: list[ExtractedUnit] = Field(min_length=1)
    # Warnings and provider are server-owned labels; they are normalized (and, for
    # provider output, replaced) so untrusted text cannot be smuggled to the UI.
    warnings: list[str] = Field(default_factory=list)
    provider: str = Field(default="rule_based", max_length=64)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("warnings", mode="before")
    @classmethod
    def clean_warnings(cls, value: object) -> object:
        if not isinstance(value, list):
            return []
        cleaned = [str(item).strip()[:500] for item in value if str(item).strip()]
        return cleaned[:50]

    @field_validator("units")
    @classmethod
    def unique_keys(cls, units: list[ExtractedUnit]) -> list[ExtractedUnit]:
        topic_keys: set[str] = set()
        for unit in units:
            for topic in unit.topics:
                if topic.key in topic_keys:
                    raise ValueError(f"Duplicate topic key: {topic.key}")
                topic_keys.add(topic.key)
            if unit.topics:
                continue
            raise ValueError(f"Unit '{unit.title}' has no topics.")
        return units

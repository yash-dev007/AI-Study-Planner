"""Syllabus upload, extraction preview, and confirmation schemas.

The preview returned by the extractor is *not* the course content. The professor
reviews and edits it, then confirms it. Only confirmed content is schedulable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.entities import DocumentStatus

Difficulty = Literal["easy", "medium", "hard"]


class TopicPreview(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    learning_objectives: list[str] = Field(default_factory=list, max_length=20)
    estimated_duration_minutes: int = Field(default=60, ge=5, le=600)
    difficulty: Difficulty = "medium"
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    needs_review: bool = False
    prerequisite_keys: list[str] = Field(default_factory=list, max_length=20)
    sequence_number: int = Field(default=1, ge=1)

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


class UnitPreview(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    sequence_number: int = Field(ge=1)
    topics: list[TopicPreview] = Field(min_length=1)


class ExtractionStats(BaseModel):
    unit_count: int
    topic_count: int
    total_minutes: int
    needs_review_count: int
    prerequisite_count: int


class SyllabusDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    processing_status: DocumentStatus
    error_message: str
    extracted_at: datetime | None
    created_at: datetime


class ExtractionPreview(BaseModel):
    """Structured, editable extraction result awaiting professor approval."""

    document: SyllabusDocumentRead
    provider: str
    used_llm: bool
    confidence: float
    units: list[UnitPreview]
    stats: ExtractionStats
    warnings: list[str] = Field(default_factory=list)
    requires_review: bool = False
    text_excerpt: str = ""


class SyllabusConfirmRequest(BaseModel):
    """The professor's edited version of the extraction preview."""

    units: list[UnitPreview] = Field(min_length=1)
    replace_existing: bool = Field(
        default=True,
        description="Replace the current syllabus structure instead of appending to it.",
    )
    allow_schedule_regeneration: bool = Field(
        default=False,
        description=(
            "Required when the course already has an approved schedule: replacing the "
            "structure invalidates it, so the professor must confirm that the schedule "
            "will be regenerated."
        ),
    )

    @field_validator("units")
    @classmethod
    def validate_keys(cls, units: list[UnitPreview]) -> list[UnitPreview]:
        topic_keys: set[str] = set()
        for unit in units:
            for topic in unit.topics:
                if topic.key in topic_keys:
                    raise ValueError(f"Duplicate topic key: {topic.key}")
                topic_keys.add(topic.key)
            for topic in unit.topics:
                unknown = [key for key in topic.prerequisite_keys if key not in topic_keys]
                if unknown:
                    raise ValueError(
                        f"Topic '{topic.title}' references unknown prerequisite key(s): "
                        + ", ".join(unknown)
                    )
        return units


class SyllabusStructureRead(BaseModel):
    units: list[UnitRead]
    topic_count: int
    total_minutes: int
    warnings: list[str] = Field(default_factory=list)


class TopicRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    unit_id: uuid.UUID
    title: str
    description: str
    learning_objectives: list[str]
    estimated_duration_minutes: int
    difficulty: str
    sequence_number: int
    priority: int
    is_splittable: bool
    extraction_confidence: float | None
    needs_review: bool
    prerequisite_ids: list[uuid.UUID] = Field(default_factory=list)


class UnitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    title: str
    description: str
    sequence_number: int
    topics: list[TopicRead] = Field(default_factory=list)


class TopicUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    learning_objectives: list[str] | None = None
    estimated_duration_minutes: int | None = Field(default=None, ge=5, le=600)
    difficulty: Difficulty | None = None
    sequence_number: int | None = Field(default=None, ge=1)
    priority: int | None = Field(default=None, ge=1, le=5)
    is_splittable: bool | None = None
    needs_review: bool | None = None
    prerequisite_ids: list[uuid.UUID] | None = Field(
        default=None, description="Full replacement of this topic's prerequisites."
    )


SyllabusStructureRead.model_rebuild()

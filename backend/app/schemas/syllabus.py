"""Syllabus upload, extraction preview, and confirmation schemas.

The preview returned by the extractor is *not* the course content. The professor
reviews and edits it, then confirms it. Only confirmed content is schedulable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.entities import DocumentStatus

Difficulty = Literal["easy", "medium", "hard"]


def reject_explicit_nulls(model: BaseModel, fields: tuple[str, ...]) -> None:
    """Reject an explicit ``null`` for a column the database stores as NOT NULL.

    Omitting the field is a valid partial update; sending ``null`` used to reach
    SQLAlchemy and fail later as a 409 ``CONSTRAINT_VIOLATION``.
    """

    nulls = sorted(
        field
        for field in fields
        if field in model.model_fields_set and getattr(model, field) is None
    )
    if nulls:
        raise ValueError("These fields cannot be null: " + ", ".join(nulls))


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
    topics: list[TopicPreview] = Field(min_length=1, max_length=200)


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


class SyllabusTextUploadRequest(BaseModel):
    """Directly paste syllabus text without a file upload."""

    title: str = Field(default="Pasted Syllabus", min_length=1, max_length=200)
    text: str = Field(min_length=10, max_length=200_000)


class SyllabusPromptRefineRequest(BaseModel):
    """Prompt-driven instruction from the professor to modify extracted units and topics."""

    prompt: str = Field(min_length=3, max_length=4000)
    current_units: list[UnitPreview] | None = Field(
        default=None,
        max_length=100,
        description=(
            "Optional current working copy of units to refine, preserving any manual edits."
        ),
    )


class SyllabusConfirmRequest(BaseModel):
    """The professor's edited version of the extraction preview."""

    units: list[UnitPreview] = Field(min_length=1, max_length=100)
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
        # Collect every key first: a prerequisite may legitimately point at a
        # topic in a later unit, so validation must not depend on payload order.
        topic_keys: set[str] = set()
        for unit in units:
            for topic in unit.topics:
                if topic.key in topic_keys:
                    raise ValueError(f"Duplicate topic key: {topic.key}")
                topic_keys.add(topic.key)
        for unit in units:
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
    schedule_impact: ScheduleImpact


class ScheduleImpact(BaseModel):
    """Whether approved timetable rows still match the current syllabus."""

    has_approved_schedule: bool
    message: str = ""


class TopicRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    unit_id: uuid.UUID
    original_name: str = ""
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
    duration_locked: bool = False
    archived_at: datetime | None = None
    prerequisite_ids: list[uuid.UUID] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class UnitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    title: str
    description: str
    sequence_number: int
    planned_minutes: int | None = None
    archived_at: datetime | None = None
    topics: list[TopicRead] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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
    archived: bool | None = Field(
        default=None, description="Archive instead of deleting when history exists."
    )
    prerequisite_ids: list[uuid.UUID] | None = Field(
        default=None, description="Full replacement of this topic's prerequisites."
    )

    @model_validator(mode="after")
    def reject_null_fields(self) -> TopicUpdate:
        reject_explicit_nulls(
            self,
            (
                "title",
                "description",
                "learning_objectives",
                "estimated_duration_minutes",
                "difficulty",
                "sequence_number",
                "priority",
                "is_splittable",
                "needs_review",
            ),
        )
        return self


class TopicCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    learning_objectives: list[str] = Field(default_factory=list, max_length=20)
    estimated_duration_minutes: int = Field(default=60, ge=5, le=600)
    difficulty: Difficulty = "medium"
    priority: int = Field(default=3, ge=1, le=5)
    is_splittable: bool = True
    prerequisite_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class UnitCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    planned_minutes: int | None = Field(default=None, ge=5)


class UnitUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    planned_minutes: int | None = Field(default=None, ge=5)
    archived: bool | None = None

    @model_validator(mode="after")
    def reject_null_fields(self) -> UnitUpdate:
        reject_explicit_nulls(self, ("title", "description"))
        return self


class TopicSave(BaseModel):
    """One topic in a full-structure save.

    ``id`` is present for existing rows and omitted for new ones. ``None`` for
    ``prerequisite_ids`` or ``archived`` means "leave unchanged".
    """

    id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    learning_objectives: list[str] = Field(default_factory=list, max_length=20)
    estimated_duration_minutes: int = Field(default=60, ge=5, le=600)
    difficulty: Difficulty = "medium"
    priority: int = Field(default=3, ge=1, le=5)
    is_splittable: bool = True
    needs_review: bool = False
    archived: bool | None = None
    prerequisite_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)


class UnitSave(BaseModel):
    id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    planned_minutes: int | None = Field(default=None, ge=5)
    archived: bool | None = None
    topics: list[TopicSave] = Field(default_factory=list, max_length=100)


class SyllabusSaveRequest(BaseModel):
    """Full editor state. Existing rows are updated, never deleted."""

    units: list[UnitSave] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_ids(self) -> SyllabusSaveRequest:
        unit_ids: set[uuid.UUID] = set()
        topic_ids: set[uuid.UUID] = set()
        for unit in self.units:
            if unit.id is not None:
                if unit.id in unit_ids:
                    raise ValueError(f"Unit {unit.id} appears more than once.")
                unit_ids.add(unit.id)
            for topic in unit.topics:
                if topic.id is None:
                    continue
                if topic.id in topic_ids:
                    raise ValueError(f"Topic {topic.id} appears more than once.")
                topic_ids.add(topic.id)
        return self


class UnitReorderRequest(BaseModel):
    unit_ids: list[uuid.UUID] = Field(min_length=1)
    dry_run: bool = False


class TopicReorderRequest(BaseModel):
    topic_ids: list[uuid.UUID] = Field(min_length=1)
    dry_run: bool = False


class ReorderResult(BaseModel):
    applied: bool
    unit_ids: list[uuid.UUID] = Field(default_factory=list)
    topic_ids: list[uuid.UUID] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


SyllabusStructureRead.model_rebuild()

"""Syllabus upload, extraction review, confirmation, and structure management."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import CourseDep, CurrentUser, DbSession, upload_rate_limit
from app.core.config import settings
from app.core.errors import DocumentProcessingError
from app.models.entities import Course
from app.schemas.common import MessageResponse
from app.schemas.syllabus import (
    ExtractionPreview,
    ReorderResult,
    SyllabusConfirmRequest,
    SyllabusDocumentRead,
    SyllabusPromptRefineRequest,
    SyllabusSaveRequest,
    SyllabusStructureRead,
    SyllabusTextUploadRequest,
    TopicCreate,
    TopicRead,
    TopicReorderRequest,
    TopicUpdate,
    UnitCreate,
    UnitRead,
    UnitReorderRequest,
    UnitUpdate,
)
from app.services import syllabus as syllabus_service
from app.services.audit import record_audit
from app.services.syllabus import (
    build_preview,
    confirm_structure,
    get_document,
    load_structure,
    refine_preview,
    upload_syllabus,
    upload_text_syllabus,
)

router = APIRouter(prefix="/courses/{course_id}/syllabus", tags=["syllabus"])

MAX_UPLOAD_BYTES = settings.max_upload_mb * 1024 * 1024


def _structure(
    db: Session, course: Course, warnings: list[str] | None = None
) -> SyllabusStructureRead:
    """Read the editor view. Summaries count active rows only.

    Archived units and topics are still listed so they can be restored, but they
    never contribute to planning totals or new schedules.
    """

    units = load_structure(db, course)
    prerequisites = syllabus_service.prerequisite_map(db, course)
    active_topics = [
        topic
        for unit in units
        if unit.archived_at is None
        for topic in unit.topics
        if topic.archived_at is None
    ]
    return SyllabusStructureRead(
        units=[syllabus_service.unit_read(db, unit, prerequisites=prerequisites) for unit in units],
        topic_count=len(active_topics),
        total_minutes=sum(topic.estimated_duration_minutes for topic in active_topics),
        warnings=warnings or [],
        schedule_impact=syllabus_service.schedule_impact(db, course),
    )


@router.post(
    "/upload",
    response_model=ExtractionPreview,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(upload_rate_limit)],
    summary="Upload a PDF, DOCX, or TXT syllabus and extract its structure",
)
async def upload(
    course: CourseDep,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(..., description="Syllabus document (PDF, DOCX, TXT)"),
) -> ExtractionPreview:
    # Read in bounded chunks so an oversized upload is rejected without first
    # buffering the whole file in memory.
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise DocumentProcessingError(
                f"The file exceeds the {settings.max_upload_mb} MB upload limit.",
                code="FILE_TOO_LARGE",
                details={"size_bytes": total, "limit_bytes": MAX_UPLOAD_BYTES},
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    document = upload_syllabus(
        db,
        course=course,
        user=user,
        filename=file.filename or "syllabus",
        content_type=file.content_type or "",
        data=data,
    )
    preview = build_preview(document)
    db.commit()
    return preview


@router.post(
    "/text",
    response_model=ExtractionPreview,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(upload_rate_limit)],
    summary="Extract syllabus structure directly from pasted text",
)
def upload_text(
    course: CourseDep,
    payload: SyllabusTextUploadRequest,
    db: DbSession,
    user: CurrentUser,
) -> ExtractionPreview:
    document = upload_text_syllabus(
        db,
        course=course,
        user=user,
        title=payload.title,
        text=payload.text,
    )
    preview = build_preview(document)
    db.commit()
    return preview


@router.get("", response_model=list[SyllabusDocumentRead], summary="Uploaded syllabus documents")
def list_documents(course: CourseDep, db: DbSession) -> list[SyllabusDocumentRead]:
    return [
        SyllabusDocumentRead.model_validate(doc)
        for doc in syllabus_service.list_documents(db, course=course)
    ]


@router.get(
    "/documents/{document_id}/preview",
    response_model=ExtractionPreview,
    summary="Structured extraction preview awaiting confirmation",
)
def preview(document_id: uuid.UUID, course: CourseDep, db: DbSession) -> ExtractionPreview:
    document = get_document(db, course=course, document_id=document_id)
    return build_preview(document)


@router.post(
    "/documents/{document_id}/refine",
    response_model=ExtractionPreview,
    dependencies=[Depends(upload_rate_limit)],
    summary="Refine an extraction preview using an AI prompt",
)
def refine(
    course: CourseDep,
    document_id: uuid.UUID,
    payload: SyllabusPromptRefineRequest,
    db: DbSession,
    user: CurrentUser,
) -> ExtractionPreview:
    preview = refine_preview(
        db,
        course=course,
        user=user,
        document_id=document_id,
        prompt=payload.prompt,
        current_units=payload.current_units,
    )
    db.commit()
    return preview


@router.put(
    "",
    response_model=SyllabusStructureRead,
    summary="Save the full syllabus structure from the editor",
)
def save_structure(
    course: CourseDep, payload: SyllabusSaveRequest, db: DbSession, user: CurrentUser
) -> SyllabusStructureRead:
    units, warnings = syllabus_service.save_structure(db, course=course, payload=payload)
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="syllabus.saved",
        entity_type="course",
        entity_id=str(course.id),
        detail={
            "unit_count": len(units),
            "topic_count": sum(len(unit.topics) for unit in units),
        },
    )
    db.commit()
    return _structure(db, course, warnings)


@router.post(
    "/confirm",
    response_model=SyllabusStructureRead,
    status_code=status.HTTP_201_CREATED,
    summary="Confirm the reviewed structure as the course syllabus",
)
def confirm(
    course: CourseDep, payload: SyllabusConfirmRequest, db: DbSession, user: CurrentUser
) -> SyllabusStructureRead:
    _, warnings = confirm_structure(db, course=course, user=user, payload=payload)
    db.commit()
    return _structure(db, course, warnings)


@router.get(
    "/structure", response_model=SyllabusStructureRead, summary="Confirmed syllabus structure"
)
def structure(course: CourseDep, db: DbSession) -> SyllabusStructureRead:
    return _structure(db, course)


# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #


@router.post(
    "/units",
    response_model=UnitRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a unit",
)
def create_unit(
    course: CourseDep, payload: UnitCreate, db: DbSession, user: CurrentUser
) -> UnitRead:
    unit = syllabus_service.create_unit(db, course=course, payload=payload)
    course.version += 1
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="syllabus.unit_created",
        entity_type="syllabus_unit",
        entity_id=str(unit.id),
    )
    db.commit()
    warnings = syllabus_service.schedule_warnings(db, course)
    return syllabus_service.unit_read(db, unit, warnings=warnings)


@router.post(
    "/units/reorder",
    response_model=ReorderResult,
    summary="Reorder units (validated first, then applied atomically)",
)
def reorder_units(
    course: CourseDep, payload: UnitReorderRequest, db: DbSession, user: CurrentUser
) -> ReorderResult:
    units, warnings = syllabus_service.reorder_units(
        db, course=course, unit_ids=payload.unit_ids, dry_run=payload.dry_run
    )
    if not payload.dry_run:
        record_audit(
            db,
            user_id=user.id,
            course_id=course.id,
            action="syllabus.units_reordered",
            entity_type="course",
            entity_id=str(course.id),
            detail={"unit_count": len(units)},
        )
        db.commit()
    return ReorderResult(
        applied=not payload.dry_run,
        unit_ids=[unit.id for unit in units],
        warnings=warnings,
    )


@router.patch(
    "/units/{unit_id}",
    response_model=UnitRead,
    summary="Rename, re-cap, archive, or restore a unit",
)
def update_unit(
    course: CourseDep,
    unit_id: uuid.UUID,
    payload: UnitUpdate,
    db: DbSession,
    user: CurrentUser,
) -> UnitRead:
    unit, archived = syllabus_service.update_unit(
        db, course=course, unit_id=unit_id, payload=payload
    )
    course.version += 1
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="syllabus.unit_archived" if archived else "syllabus.unit_updated",
        entity_type="syllabus_unit",
        entity_id=str(unit_id),
        detail={"fields": sorted(payload.model_fields_set)},
    )
    db.commit()
    warnings = syllabus_service.schedule_warnings(db, course, archived=archived)
    return syllabus_service.unit_read(db, unit, warnings=warnings)


@router.delete(
    "/units/{unit_id}",
    response_model=MessageResponse,
    summary="Delete a unit, or archive it when history would be lost",
)
def delete_unit(
    course: CourseDep,
    unit_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    archive: Annotated[
        bool, Query(description="Archive instead of deleting; required when history exists.")
    ] = False,
) -> MessageResponse:
    _, archived = syllabus_service.delete_unit(db, course=course, unit_id=unit_id, archive=archive)
    course.version += 1
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="syllabus.unit_archived" if archived else "syllabus.unit_deleted",
        entity_type="syllabus_unit",
        entity_id=str(unit_id),
    )
    db.commit()
    return MessageResponse(
        message=(
            "Unit archived. It stays readable in history and is excluded from new schedules."
            if archived
            else "Unit deleted. Generate a new schedule to reflect it."
        )
    )


# --------------------------------------------------------------------------- #
# Topics
# --------------------------------------------------------------------------- #


@router.post(
    "/units/{unit_id}/topics",
    response_model=TopicRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a topic to a unit",
)
def create_topic(
    course: CourseDep,
    unit_id: uuid.UUID,
    payload: TopicCreate,
    db: DbSession,
    user: CurrentUser,
) -> TopicRead:
    topic = syllabus_service.create_topic(db, course=course, unit_id=unit_id, payload=payload)
    course.version += 1
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="syllabus.topic_created",
        entity_type="topic",
        entity_id=str(topic.id),
    )
    db.commit()
    warnings = syllabus_service.schedule_warnings(db, course)
    return syllabus_service.topic_read(db, topic, warnings=warnings)


@router.post(
    "/units/{unit_id}/topics/reorder",
    response_model=ReorderResult,
    summary="Reorder the topics of one unit",
)
def reorder_topics(
    course: CourseDep,
    unit_id: uuid.UUID,
    payload: TopicReorderRequest,
    db: DbSession,
    user: CurrentUser,
) -> ReorderResult:
    topics, warnings = syllabus_service.reorder_topics(
        db,
        course=course,
        unit_id=unit_id,
        topic_ids=payload.topic_ids,
        dry_run=payload.dry_run,
    )
    if not payload.dry_run:
        record_audit(
            db,
            user_id=user.id,
            course_id=course.id,
            action="syllabus.topics_reordered",
            entity_type="syllabus_unit",
            entity_id=str(unit_id),
            detail={"topic_count": len(topics)},
        )
        db.commit()
    return ReorderResult(
        applied=not payload.dry_run,
        unit_ids=[unit_id],
        topic_ids=[topic.id for topic in topics],
        warnings=warnings,
    )


@router.patch(
    "/topics/{topic_id}",
    response_model=TopicRead,
    summary="Edit a topic (duration, prerequisites, splitting, priority, archive)",
)
def update_topic(
    course: CourseDep,
    topic_id: uuid.UUID,
    payload: TopicUpdate,
    db: DbSession,
    user: CurrentUser,
) -> TopicRead:
    topic, archived = syllabus_service.update_topic(
        db, course=course, topic_id=topic_id, payload=payload
    )
    course.version += 1
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="syllabus.topic_archived" if archived else "syllabus.topic_updated",
        entity_type="topic",
        entity_id=str(topic_id),
        detail={"fields": sorted(payload.model_dump(exclude_unset=True))},
    )
    db.commit()
    warnings = syllabus_service.schedule_warnings(db, course, archived=archived)
    return syllabus_service.topic_read(db, topic, warnings=warnings)


@router.delete(
    "/topics/{topic_id}",
    response_model=MessageResponse,
    summary="Delete a topic, or archive it when lecture history exists",
)
def delete_topic(
    course: CourseDep,
    topic_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    archive: Annotated[
        bool, Query(description="Archive instead of deleting; required when history exists.")
    ] = False,
) -> MessageResponse:
    _, archived = syllabus_service.delete_topic(
        db, course=course, topic_id=topic_id, archive=archive
    )
    course.version += 1
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="syllabus.topic_archived" if archived else "syllabus.topic_deleted",
        entity_type="topic",
        entity_id=str(topic_id),
    )
    db.commit()
    return MessageResponse(
        message=(
            "Topic archived. It stays readable in history and is excluded from new schedules."
            if archived
            else "Topic deleted. Generate a new schedule to reflect it."
        )
    )

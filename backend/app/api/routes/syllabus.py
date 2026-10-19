"""Syllabus upload, extraction review, and confirmation routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, UploadFile, status

from app.api.deps import CourseDep, CurrentUser, DbSession, upload_rate_limit
from app.core.config import settings
from app.core.errors import DocumentProcessingError
from app.schemas.common import MessageResponse
from app.schemas.syllabus import (
    ExtractionPreview,
    SyllabusConfirmRequest,
    SyllabusDocumentRead,
    SyllabusStructureRead,
    TopicRead,
    TopicUpdate,
    UnitRead,
)
from app.services import syllabus as syllabus_service
from app.services.audit import record_audit
from app.services.syllabus import (
    build_preview,
    confirm_structure,
    get_document,
    load_structure,
    upload_syllabus,
)

router = APIRouter(prefix="/courses/{course_id}/syllabus", tags=["syllabus"])

MAX_UPLOAD_BYTES = settings.max_upload_mb * 1024 * 1024


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
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentProcessingError(
            f"The file exceeds the {settings.max_upload_mb} MB upload limit.",
            code="FILE_TOO_LARGE",
            details={"size_bytes": len(data), "limit_bytes": MAX_UPLOAD_BYTES},
        )
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
    structure = load_structure(db, course)
    return SyllabusStructureRead(
        units=[UnitRead.model_validate(unit) for unit in structure],
        topic_count=sum(len(unit.topics) for unit in structure),
        total_minutes=sum(
            topic.estimated_duration_minutes for unit in structure for topic in unit.topics
        ),
        warnings=warnings,
    )


@router.get(
    "/structure", response_model=SyllabusStructureRead, summary="Confirmed syllabus structure"
)
def structure(course: CourseDep, db: DbSession) -> SyllabusStructureRead:
    units = load_structure(db, course)
    return SyllabusStructureRead(
        units=[UnitRead.model_validate(unit) for unit in units],
        topic_count=sum(len(unit.topics) for unit in units),
        total_minutes=sum(
            topic.estimated_duration_minutes for unit in units for topic in unit.topics
        ),
    )


@router.patch(
    "/topics/{topic_id}",
    response_model=TopicRead,
    summary="Edit a topic (duration, prerequisites, splitting, priority)",
)
def update_topic(
    course: CourseDep,
    topic_id: uuid.UUID,
    payload: TopicUpdate,
    db: DbSession,
    user: CurrentUser,
) -> TopicRead:
    topic, _ = syllabus_service.update_topic(db, course=course, topic_id=topic_id, payload=payload)
    course.version += 1
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="syllabus.topic_updated",
        entity_type="topic",
        entity_id=str(topic.id),
        detail={"fields": sorted(payload.model_dump(exclude_unset=True))},
    )
    db.commit()
    return syllabus_service.topic_read(db, topic)


@router.delete(
    "/topics/{topic_id}",
    response_model=MessageResponse,
    summary="Delete a topic",
)
def delete_topic(
    course: CourseDep, topic_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> MessageResponse:
    topic = syllabus_service.delete_topic(db, course=course, topic_id=topic_id)
    course.version += 1
    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="syllabus.topic_deleted",
        entity_type="topic",
        entity_id=str(topic.id),
    )
    db.commit()
    return MessageResponse(message="Topic deleted. Generate a new schedule to reflect it.")

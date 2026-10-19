"""Syllabus upload, extraction preview, and confirmation.

Nothing extracted from a document is schedulable until the professor confirms
it. The upload endpoint stores the file, extracts text, runs the (optional) LLM
interpreter with a deterministic fallback, and persists the *preview* as
document metadata. Confirmation writes the course's real syllabus structure.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.ai.llm import build_llm_interpreter
from app.ai.schemas import SyllabusExtraction
from app.ai.service import SyllabusExtractionService
from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError
from app.documents.extractor import extract_text
from app.models.entities import (
    ApprovalStatus,
    Course,
    DependencyType,
    DocumentStatus,
    NotificationType,
    ScheduleRevision,
    SyllabusDocument,
    SyllabusUnit,
    Topic,
    TopicDependency,
    User,
)
from app.schemas.syllabus import (
    ExtractionPreview,
    ExtractionStats,
    SyllabusConfirmRequest,
    SyllabusDocumentRead,
    TopicPreview,
    TopicRead,
    TopicUpdate,
    UnitPreview,
)
from app.services.audit import notify, record_audit

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
MAX_FILENAME_LENGTH = 120


def safe_filename(filename: str) -> str:
    name = Path(filename or "syllabus").name
    cleaned = _SAFE_NAME.sub("_", name).strip("._") or "syllabus"
    return cleaned[:MAX_FILENAME_LENGTH]


def storage_path(course_id: uuid.UUID, document_id: uuid.UUID, filename: str) -> Path:
    target = settings.storage_dir / "syllabi" / str(course_id) / str(document_id)
    target.mkdir(parents=True, exist_ok=True)
    return target / safe_filename(filename)


def _extraction_service() -> SyllabusExtractionService:
    llm = build_llm_interpreter(settings) if settings.llm_enabled else None
    return SyllabusExtractionService(settings, llm=llm)


def upload_syllabus(
    db: Session,
    *,
    course: Course,
    user: User,
    filename: str,
    content_type: str,
    data: bytes,
) -> SyllabusDocument:
    """Store and interpret an uploaded syllabus. Returns the document row.

    The raw file is written to storage before interpretation starts so a failed
    extraction still leaves an auditable record of what was uploaded.
    """

    document = SyllabusDocument(
        course_id=course.id,
        filename=safe_filename(filename),
        content_type=(content_type or "")[:120],
        size_bytes=len(data),
        storage_reference="",
        processing_status=DocumentStatus.UPLOADED,
    )
    db.add(document)
    db.flush()

    path = storage_path(course.id, document.id, filename)
    path.write_bytes(data)
    document.storage_reference = str(path)

    try:
        extracted = extract_text(filename, content_type, data)
    except Exception as exc:
        document.processing_status = DocumentStatus.FAILED
        document.error_message = getattr(exc, "message", None) or str(exc)
        db.flush()
        record_audit(
            db,
            user_id=user.id,
            course_id=course.id,
            action="syllabus.upload_failed",
            entity_type="syllabus_document",
            entity_id=str(document.id),
            detail={"filename": document.filename, "reason": document.error_message},
        )
        # The upload failed, but the attempt belongs in the audit trail, so it is
        # committed before the error propagates and the request rolls back.
        db.commit()
        raise

    document.processing_status = DocumentStatus.EXTRACTING
    document.extracted_text = extracted.text

    outcome = _extraction_service().extract(extracted.text, ocr_used=extracted.ocr_used)
    units = _to_preview_units(outcome.extraction)
    stats = _stats(units)
    warnings = list(outcome.warnings) + list(extracted.warnings)

    document.extraction_metadata = {
        "provider": outcome.provider,
        "used_llm": outcome.used_llm,
        "confidence": outcome.extraction.confidence,
        "units": [unit.model_dump(mode="json") for unit in units],
        "stats": stats.model_dump(),
        "warnings": warnings,
        "page_count": extracted.page_count,
        "ocr_used": extracted.ocr_used,
        "ocr_pages": extracted.ocr_pages,
        "char_count": len(extracted.text),
        "text_excerpt": extracted.text[:800],
    }
    document.extracted_at = datetime.now(UTC)
    document.processing_status = (
        DocumentStatus.NEEDS_REVIEW if stats.needs_review_count else DocumentStatus.EXTRACTED
    )
    db.flush()

    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="syllabus.uploaded",
        entity_type="syllabus_document",
        entity_id=str(document.id),
        detail={
            "filename": document.filename,
            "provider": outcome.provider,
            "unit_count": stats.unit_count,
            "topic_count": stats.topic_count,
            "ocr_used": extracted.ocr_used,
        },
    )
    notify(
        db,
        user_id=user.id,
        course_id=course.id,
        event_type=NotificationType.SYLLABUS_PROCESSED,
        message=(
            f"Syllabus '{document.filename}' was processed: {stats.unit_count} unit(s), "
            f"{stats.topic_count} topic(s). Review and confirm the structure."
        ),
    )
    return document


def _to_preview_units(extraction: SyllabusExtraction) -> list[UnitPreview]:
    units: list[UnitPreview] = []
    for unit_index, unit in enumerate(extraction.units, start=1):
        topics: list[TopicPreview] = []
        for topic_index, topic in enumerate(unit.topics, start=1):
            topics.append(
                TopicPreview(
                    key=topic.key,
                    title=topic.title,
                    description=topic.description,
                    learning_objectives=list(topic.learning_objectives),
                    estimated_duration_minutes=topic.estimated_duration_minutes,
                    difficulty=topic.difficulty,
                    confidence=topic.confidence,
                    needs_review=topic.needs_review,
                    prerequisite_keys=list(topic.prerequisite_keys),
                    sequence_number=topic_index,
                )
            )
        units.append(
            UnitPreview(
                key=unit.key,
                title=unit.title,
                description=unit.description,
                sequence_number=unit.sequence_number or unit_index,
                topics=topics,
            )
        )
    return units


def _stats(units: list[UnitPreview]) -> ExtractionStats:
    topic_count = sum(len(unit.topics) for unit in units)
    total_minutes = sum(topic.estimated_duration_minutes for unit in units for topic in unit.topics)
    needs_review = sum(
        1
        for unit in units
        for topic in unit.topics
        if topic.needs_review or topic.confidence < 0.65
    )
    prerequisites = sum(len(topic.prerequisite_keys) for unit in units for topic in unit.topics)
    return ExtractionStats(
        unit_count=len(units),
        topic_count=topic_count,
        total_minutes=total_minutes,
        needs_review_count=needs_review,
        prerequisite_count=prerequisites,
    )


def get_document(db: Session, *, course: Course, document_id: uuid.UUID) -> SyllabusDocument:
    document = db.get(SyllabusDocument, document_id)
    if document is None or document.course_id != course.id:
        raise NotFoundError("Syllabus document not found.", code="DOCUMENT_NOT_FOUND")
    return document


def build_preview(document: SyllabusDocument) -> ExtractionPreview:
    metadata = document.extraction_metadata or {}
    units = [UnitPreview.model_validate(item) for item in metadata.get("units", [])]
    stats_data = metadata.get("stats")
    stats = ExtractionStats.model_validate(stats_data) if stats_data else _stats(units)
    confidence = float(metadata.get("confidence", 0.7) or 0.7)
    return ExtractionPreview(
        document=SyllabusDocumentRead.model_validate(document),
        provider=str(metadata.get("provider", "rule_based")),
        used_llm=bool(metadata.get("used_llm", False)),
        confidence=confidence,
        units=units,
        stats=stats,
        warnings=list(metadata.get("warnings", [])),
        requires_review=stats.needs_review_count > 0 or confidence < 0.6,
        text_excerpt=str(metadata.get("text_excerpt", "")),
    )


def list_documents(db: Session, *, course: Course) -> list[SyllabusDocument]:
    return list(
        db.execute(
            select(SyllabusDocument)
            .where(SyllabusDocument.course_id == course.id)
            .order_by(SyllabusDocument.created_at.desc())
        )
        .scalars()
        .all()
    )


def approved_revision_exists(db: Session, course: Course) -> bool:
    return (
        db.execute(
            select(ScheduleRevision.id)
            .where(
                ScheduleRevision.course_id == course.id,
                ScheduleRevision.approval_status == ApprovalStatus.APPROVED,
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def load_structure(db: Session, course: Course) -> list[SyllabusUnit]:
    return list(
        db.execute(
            select(SyllabusUnit)
            .where(SyllabusUnit.course_id == course.id)
            .order_by(SyllabusUnit.sequence_number)
            .options(selectinload(SyllabusUnit.topics))
        )
        .scalars()
        .all()
    )


def confirm_structure(
    db: Session, *, course: Course, user: User, payload: SyllabusConfirmRequest
) -> tuple[list[SyllabusUnit], list[str]]:
    """Persist the professor-approved syllabus structure.

    Refuses to destroy the structure an approved schedule was built from unless
    the caller explicitly accepts that the schedule has to be regenerated.
    """

    warnings: list[str] = []
    has_schedule = approved_revision_exists(db, course)

    if has_schedule and payload.replace_existing and not payload.allow_schedule_regeneration:
        raise ConflictError(
            "This course already has an approved schedule built from the current topics. "
            "Confirming a new structure invalidates it, so the schedule must be regenerated. "
            "Set allow_schedule_regeneration to continue.",
            code="SYLLABUS_REPLACES_SCHEDULE",
            details={"requires_explicit_confirmation": True},
        )

    existing_units = load_structure(db, course)
    existing_topic_count = sum(len(unit.topics) for unit in existing_units)

    if existing_units and payload.replace_existing:
        warnings.append(
            f"{len(existing_units)} existing unit(s) and {existing_topic_count} topic(s) "
            "were replaced by the confirmed syllabus."
        )
        for unit in existing_units:
            db.delete(unit)
        db.flush()
        start_sequence = 1
        title_index: dict[str, Topic] = {}
    else:
        start_sequence = max((unit.sequence_number for unit in existing_units), default=0) + 1
        title_index = {
            topic.title.strip().lower(): topic for unit in existing_units for topic in unit.topics
        }

    key_to_topic: dict[str, Topic] = {}
    created_units: list[SyllabusUnit] = []

    for offset, unit_payload in enumerate(payload.units):
        unit = SyllabusUnit(
            course_id=course.id,
            title=unit_payload.title.strip(),
            description=unit_payload.description.strip(),
            sequence_number=start_sequence + offset,
        )
        db.add(unit)
        db.flush()
        created_units.append(unit)
        for topic_index, topic_payload in enumerate(unit_payload.topics, start=1):
            topic = Topic(
                unit_id=unit.id,
                title=topic_payload.title.strip(),
                description=topic_payload.description.strip(),
                learning_objectives=list(topic_payload.learning_objectives),
                estimated_duration_minutes=topic_payload.estimated_duration_minutes,
                difficulty=topic_payload.difficulty,
                sequence_number=topic_index,
                priority=3,
                is_splittable=True,
                extraction_confidence=topic_payload.confidence,
                needs_review=topic_payload.needs_review,
            )
            db.add(topic)
            key_to_topic[topic_payload.key] = topic
        db.flush()

    unresolved: list[str] = []
    for unit_payload in payload.units:
        for topic_payload in unit_payload.topics:
            topic = key_to_topic[topic_payload.key]
            for prerequisite_key in topic_payload.prerequisite_keys:
                prerequisite = key_to_topic.get(prerequisite_key) or title_index.get(
                    prerequisite_key.strip().lower()
                )
                if prerequisite is None:
                    unresolved.append(prerequisite_key)
                    topic.needs_review = True
                    continue
                if prerequisite.id == topic.id:
                    continue
                db.add(
                    TopicDependency(
                        topic_id=topic.id,
                        prerequisite_topic_id=prerequisite.id,
                        dependency_type=DependencyType.PREREQUISITE,
                    )
                )
    if unresolved:
        warnings.append(
            "Some prerequisites could not be matched to a topic and were skipped: "
            + ", ".join(sorted(set(unresolved)))
        )
    db.flush()

    course.version += 1
    db.flush()

    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="syllabus.confirmed",
        entity_type="course",
        entity_id=str(course.id),
        detail={
            "unit_count": len(created_units),
            "topic_count": len(key_to_topic),
            "replaced_existing": bool(existing_units and payload.replace_existing),
            "schedule_invalidated": has_schedule and payload.replace_existing,
        },
    )
    if has_schedule and payload.replace_existing:
        warnings.append(
            "The approved schedule still references the previous topics. Generate a new "
            "schedule so the timetable matches the confirmed syllabus."
        )
    return load_structure(db, course), warnings


def structure_topic_count(db: Session, course: Course) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(Topic)
            .join(SyllabusUnit, Topic.unit_id == SyllabusUnit.id)
            .where(SyllabusUnit.course_id == course.id)
        ).scalar_one()
    )


def _load_topic(db: Session, *, course: Course, topic_id: uuid.UUID) -> Topic:
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise NotFoundError("Topic not found.", code="TOPIC_NOT_FOUND")
    unit = db.get(SyllabusUnit, topic.unit_id)
    if unit is None or unit.course_id != course.id:
        raise NotFoundError("Topic not found.", code="TOPIC_NOT_FOUND")
    return topic


def prerequisite_ids(db: Session, topic_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        db.execute(
            select(TopicDependency.prerequisite_topic_id).where(
                TopicDependency.topic_id == topic_id
            )
        )
        .scalars()
        .all()
    )


def topic_read(db: Session, topic: Topic) -> TopicRead:
    from app.schemas.syllabus import TopicRead

    return TopicRead.model_validate(
        {
            "id": topic.id,
            "unit_id": topic.unit_id,
            "title": topic.title,
            "description": topic.description,
            "learning_objectives": topic.learning_objectives,
            "estimated_duration_minutes": topic.estimated_duration_minutes,
            "difficulty": topic.difficulty,
            "sequence_number": topic.sequence_number,
            "priority": topic.priority,
            "is_splittable": topic.is_splittable,
            "extraction_confidence": topic.extraction_confidence,
            "needs_review": topic.needs_review,
            "prerequisite_ids": prerequisite_ids(db, topic.id),
        }
    )


def update_topic(
    db: Session, *, course: Course, topic_id: uuid.UUID, payload: TopicUpdate
) -> tuple[Topic, list[uuid.UUID]]:
    """Apply a partial topic update, including a full prerequisite replacement."""

    topic = _load_topic(db, course=course, topic_id=topic_id)
    data = payload.model_dump(exclude_unset=True, exclude={"prerequisite_ids"})
    for field, value in data.items():
        setattr(topic, field, value)

    if payload.prerequisite_ids is not None:
        known = {
            row.id
            for row in db.execute(
                select(Topic)
                .join(SyllabusUnit, Topic.unit_id == SyllabusUnit.id)
                .where(SyllabusUnit.course_id == course.id)
            )
            .scalars()
            .all()
        }
        unknown = [item for item in payload.prerequisite_ids if item not in known]
        if unknown:
            raise NotFoundError(
                "A prerequisite topic does not belong to this course.",
                code="PREREQUISITE_NOT_FOUND",
                details={"topic_ids": [str(item) for item in unknown]},
            )
        for existing in (
            db.execute(select(TopicDependency).where(TopicDependency.topic_id == topic.id))
            .scalars()
            .all()
        ):
            db.delete(existing)
        db.flush()
        for prerequisite_id in payload.prerequisite_ids:
            if prerequisite_id == topic.id:
                continue
            db.add(
                TopicDependency(
                    topic_id=topic.id,
                    prerequisite_topic_id=prerequisite_id,
                    dependency_type=DependencyType.PREREQUISITE,
                )
            )
    db.flush()
    return topic, prerequisite_ids(db, topic.id)


def delete_topic(db: Session, *, course: Course, topic_id: uuid.UUID) -> Topic:
    topic = _load_topic(db, course=course, topic_id=topic_id)
    db.delete(topic)
    db.flush()
    return topic

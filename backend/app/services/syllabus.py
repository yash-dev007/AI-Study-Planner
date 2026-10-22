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
from app.ai.schemas import ExtractedTopic, ExtractedUnit, SyllabusExtraction
from app.ai.service import SyllabusExtractionService
from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.documents.extractor import extract_text
from app.models.entities import (
    ApprovalStatus,
    Course,
    DependencyType,
    DocumentStatus,
    LectureSession,
    NotificationType,
    ScheduleRevision,
    SessionStatus,
    SyllabusDocument,
    SyllabusUnit,
    Topic,
    TopicAllocation,
    TopicDependency,
    User,
)
from app.schemas.syllabus import (
    ExtractionPreview,
    ExtractionStats,
    ScheduleImpact,
    SyllabusConfirmRequest,
    SyllabusDocumentRead,
    SyllabusSaveRequest,
    TopicCreate,
    TopicPreview,
    TopicRead,
    TopicSave,
    TopicUpdate,
    UnitCreate,
    UnitPreview,
    UnitRead,
    UnitSave,
    UnitUpdate,
)
from app.services.audit import notify, record_audit
from app.services.settings import get_or_create_user_settings

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
MAX_FILENAME_LENGTH = 120

# Sessions whose recorded delivery is immutable history (BUILD_CONTRACT I4).
CLOSED_SESSION_STATUSES = (
    SessionStatus.COMPLETED,
    SessionStatus.MISSED,
    SessionStatus.CANCELLED,
    SessionStatus.RESCHEDULED,
)


def safe_filename(filename: str) -> str:
    name = Path(filename or "syllabus").name
    cleaned = _SAFE_NAME.sub("_", name).strip("._") or "syllabus"
    return cleaned[:MAX_FILENAME_LENGTH]


def storage_path(course_id: uuid.UUID, document_id: uuid.UUID, filename: str) -> Path:
    target = settings.storage_dir / "syllabi" / str(course_id) / str(document_id)
    target.mkdir(parents=True, exist_ok=True)
    return target / safe_filename(filename)


def _extraction_service(db: Session, user: User) -> SyllabusExtractionService:
    """Build the extractor with this teacher's AI preference applied (I8).

    AI is opt-in per teacher: the server configuration enables the feature, but
    an upload is only sent to a provider when this user turned AI on. The
    teacher's provider/model choice overrides the server default for this call.
    """

    if not settings.llm_enabled:
        return SyllabusExtractionService(settings, llm=None)
    row = get_or_create_user_settings(db, user)
    if not row.ai_enabled:
        return SyllabusExtractionService(settings, llm=None)

    provider = (row.preferred_ai_provider or "none").strip().lower()
    updates: dict[str, object] = {"llm_provider": provider}
    if row.preferred_ai_model:
        model_field = {
            "openrouter": "openrouter_model",
            "gemini": "gemini_model",
            "ollama": "ollama_model",
        }.get(provider)
        if model_field:
            updates[model_field] = row.preferred_ai_model
    effective = settings.model_copy(update=updates)
    return SyllabusExtractionService(settings, llm=build_llm_interpreter(effective))


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

    outcome = _extraction_service(db, user).extract(extracted.text)
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


def upload_text_syllabus(
    db: Session,
    *,
    course: Course,
    user: User,
    title: str,
    text: str,
) -> SyllabusDocument:
    """Store and interpret raw syllabus text provided by the professor. Returns the document row."""

    filename = safe_filename((title.strip() or "syllabus") + ".txt")
    data = text.encode("utf-8")
    document = SyllabusDocument(
        course_id=course.id,
        filename=filename,
        content_type="text/plain",
        size_bytes=len(data),
        storage_reference="",
        processing_status=DocumentStatus.UPLOADED,
    )
    db.add(document)
    db.flush()

    path = storage_path(course.id, document.id, filename)
    path.write_bytes(data)
    document.storage_reference = str(path)

    document.processing_status = DocumentStatus.EXTRACTING
    document.extracted_text = text

    outcome = _extraction_service(db, user).extract(text)
    units = _to_preview_units(outcome.extraction)
    stats = _stats(units)
    warnings = list(outcome.warnings)

    document.extraction_metadata = {
        "provider": outcome.provider,
        "used_llm": outcome.used_llm,
        "confidence": outcome.extraction.confidence,
        "units": [unit.model_dump(mode="json") for unit in units],
        "stats": stats.model_dump(),
        "warnings": warnings,
        "page_count": 1,
        "char_count": len(text),
        "text_excerpt": text[:800],
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
        action="syllabus.text_uploaded",
        entity_type="syllabus_document",
        entity_id=str(document.id),
        detail={
            "filename": document.filename,
            "provider": outcome.provider,
            "unit_count": stats.unit_count,
            "topic_count": stats.topic_count,
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


def _units_to_extraction(units: list[UnitPreview]) -> SyllabusExtraction:
    return SyllabusExtraction(
        units=[
            ExtractedUnit(
                key=unit.key,
                title=unit.title,
                description=unit.description,
                sequence_number=unit.sequence_number,
                topics=[
                    ExtractedTopic(
                        key=topic.key,
                        title=topic.title,
                        description=topic.description,
                        learning_objectives=list(topic.learning_objectives),
                        estimated_duration_minutes=topic.estimated_duration_minutes,
                        difficulty=topic.difficulty,
                        confidence=topic.confidence,
                        needs_review=topic.needs_review,
                        prerequisite_keys=list(topic.prerequisite_keys),
                    )
                    for topic in unit.topics
                ],
            )
            for unit in units
        ],
        confidence=0.85,
    )


def refine_preview(
    db: Session,
    *,
    course: Course,
    user: User,
    document_id: uuid.UUID,
    prompt: str,
    current_units: list[UnitPreview] | None = None,
) -> ExtractionPreview:
    """Apply an AI refinement prompt to an existing extraction preview."""

    document = get_document(db, course=course, document_id=document_id)

    if current_units:
        base_extraction = _units_to_extraction(current_units)
    else:
        metadata = document.extraction_metadata or {}
        stored_units = [UnitPreview.model_validate(u) for u in metadata.get("units", [])]
        base_extraction = _units_to_extraction(stored_units)

    outcome = _extraction_service(db, user).refine(base_extraction, prompt)
    new_units = _to_preview_units(outcome.extraction)
    new_stats = _stats(new_units)

    metadata = dict(document.extraction_metadata or {})
    metadata["provider"] = outcome.provider
    metadata["used_llm"] = outcome.used_llm
    metadata["confidence"] = outcome.extraction.confidence
    metadata["units"] = [unit.model_dump(mode="json") for unit in new_units]
    metadata["stats"] = new_stats.model_dump()
    metadata["warnings"] = list(outcome.warnings)
    document.extraction_metadata = metadata
    document.processing_status = (
        DocumentStatus.NEEDS_REVIEW if new_stats.needs_review_count else DocumentStatus.EXTRACTED
    )
    db.flush()

    record_audit(
        db,
        user_id=user.id,
        course_id=course.id,
        action="syllabus.prompt_refined",
        entity_type="syllabus_document",
        entity_id=str(document.id),
        detail={
            "prompt": prompt,
            "unit_count": new_stats.unit_count,
            "topic_count": new_stats.topic_count,
        },
    )
    return build_preview(document)


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


def closed_lecture_count(db: Session, course: Course) -> int:
    """Number of lectures whose delivery is immutable history (BUILD_CONTRACT I4).

    Replacing the syllabus deletes units and topics, and the deletions cascade
    into ``topic_allocations``, which is the only record of delivered minutes.
    """

    return int(
        db.execute(
            select(func.count())
            .select_from(LectureSession)
            .where(
                LectureSession.course_id == course.id,
                LectureSession.status.in_(CLOSED_SESSION_STATUSES),
            )
        ).scalar_one()
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
    the caller explicitly accepts that the schedule has to be regenerated, and
    refuses to replace a structure that already carries recorded lecture history
    (completed, missed, cancelled, or rescheduled sessions).
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
        closed_lectures = closed_lecture_count(db, course)
        if closed_lectures:
            raise ConflictError(
                "This course has recorded lecture history. Replacing the syllabus would "
                "delete the topics those lectures were delivered against and the minutes "
                "recorded on them, so the structure cannot be replaced. Archive the units "
                "or topics you no longer need instead.",
                code="SYLLABUS_HAS_HISTORY",
                details={
                    "closed_lecture_count": closed_lectures,
                    "can_archive": True,
                },
            )
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
            .where(
                SyllabusUnit.course_id == course.id,
                SyllabusUnit.archived_at.is_(None),
                Topic.archived_at.is_(None),
            )
        ).scalar_one()
    )


def schedule_impact(db: Session, course: Course) -> ScheduleImpact:
    has_schedule = approved_revision_exists(db, course)
    return ScheduleImpact(
        has_approved_schedule=has_schedule,
        message=(
            "An approved schedule exists. Syllabus changes require generating and approving a "
            "new revision before the timetable matches the plan."
            if has_schedule
            else ""
        ),
    )


def schedule_warnings(db: Session, course: Course, *, archived: bool = False) -> list[str]:
    """Advisory messages attached to syllabus mutations."""

    warnings: list[str] = []
    if approved_revision_exists(db, course):
        warnings.append(
            "This course has an approved schedule. Generate and approve a new revision so the "
            "timetable reflects this syllabus change."
        )
    if archived:
        warnings.append(
            "Archived items remain readable in history but are excluded from new schedules."
        )
    if structure_topic_count(db, course) == 0:
        warnings.append(
            "The course has no active topics. A schedule cannot be generated until a topic is "
            "added or restored."
        )
    return warnings


def _load_unit(db: Session, *, course: Course, unit_id: uuid.UUID) -> SyllabusUnit:
    unit = db.get(SyllabusUnit, unit_id)
    if unit is None or unit.course_id != course.id:
        raise NotFoundError("Unit not found.", code="UNIT_NOT_FOUND")
    return unit


def _load_topic(db: Session, *, course: Course, topic_id: uuid.UUID) -> Topic:
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise NotFoundError("Topic not found.", code="TOPIC_NOT_FOUND")
    unit = db.get(SyllabusUnit, topic.unit_id)
    if unit is None or unit.course_id != course.id:
        raise NotFoundError("Topic not found.", code="TOPIC_NOT_FOUND")
    return topic


def _topic_allocation_count(db: Session, topic_id: uuid.UUID) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(TopicAllocation)
            .where(TopicAllocation.topic_id == topic_id)
        ).scalar_one()
    )


def _unit_allocation_count(db: Session, unit_id: uuid.UUID) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(TopicAllocation)
            .join(Topic, TopicAllocation.topic_id == Topic.id)
            .where(Topic.unit_id == unit_id)
        ).scalar_one()
    )


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


def prerequisite_map(db: Session, course: Course) -> dict[uuid.UUID, list[uuid.UUID]]:
    """All prerequisite links for a course in one query (avoids N+1 reads)."""

    rows = db.execute(
        select(TopicDependency.topic_id, TopicDependency.prerequisite_topic_id)
        .join(Topic, TopicDependency.topic_id == Topic.id)
        .join(SyllabusUnit, Topic.unit_id == SyllabusUnit.id)
        .where(SyllabusUnit.course_id == course.id)
        .order_by(TopicDependency.prerequisite_topic_id)
    ).all()
    mapping: dict[uuid.UUID, list[uuid.UUID]] = {}
    for topic_id, prerequisite_id in rows:
        mapping.setdefault(topic_id, []).append(prerequisite_id)
    return mapping


def topic_read(
    db: Session,
    topic: Topic,
    *,
    warnings: list[str] | None = None,
    prerequisites: dict[uuid.UUID, list[uuid.UUID]] | None = None,
) -> TopicRead:
    """Render one topic. Pass ``prerequisites`` to avoid a query per topic."""

    return TopicRead.model_validate(
        {
            "id": topic.id,
            "unit_id": topic.unit_id,
            "original_name": topic.original_name,
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
            "duration_locked": topic.duration_locked,
            "archived_at": topic.archived_at,
            "prerequisite_ids": (
                prerequisites.get(topic.id, [])
                if prerequisites is not None
                else prerequisite_ids(db, topic.id)
            ),
            "warnings": warnings or [],
        }
    )


def unit_read(
    db: Session,
    unit: SyllabusUnit,
    *,
    warnings: list[str] | None = None,
    prerequisites: dict[uuid.UUID, list[uuid.UUID]] | None = None,
) -> UnitRead:
    ordered = sorted(unit.topics, key=lambda topic: topic.sequence_number)
    return UnitRead(
        id=unit.id,
        course_id=unit.course_id,
        title=unit.title,
        description=unit.description,
        sequence_number=unit.sequence_number,
        planned_minutes=unit.planned_minutes,
        archived_at=unit.archived_at,
        topics=[topic_read(db, topic, prerequisites=prerequisites) for topic in ordered],
        warnings=warnings or [],
    )


def _apply_archive_flag(row: SyllabusUnit | Topic, archived: bool | None) -> bool:
    """Set or clear ``archived_at``. Returns True when the row was archived."""

    if archived is None:
        return False
    if archived and row.archived_at is None:
        row.archived_at = datetime.now(UTC)
        return True
    if not archived and row.archived_at is not None:
        row.archived_at = None
    return False


def create_unit(db: Session, *, course: Course, payload: UnitCreate) -> SyllabusUnit:
    next_sequence = (
        int(
            db.execute(
                select(func.max(SyllabusUnit.sequence_number)).where(
                    SyllabusUnit.course_id == course.id
                )
            ).scalar_one()
            or 0
        )
        + 1
    )
    unit = SyllabusUnit(
        course_id=course.id,
        title=payload.title.strip(),
        description=payload.description.strip(),
        sequence_number=next_sequence,
        planned_minutes=payload.planned_minutes,
    )
    db.add(unit)
    db.flush()
    return unit


def update_unit(
    db: Session, *, course: Course, unit_id: uuid.UUID, payload: UnitUpdate
) -> tuple[SyllabusUnit, bool]:
    unit = _load_unit(db, course=course, unit_id=unit_id)
    data = payload.model_dump(exclude_unset=True, exclude={"archived"})
    for field, value in data.items():
        setattr(unit, field, value)
    archived = _apply_archive_flag(unit, payload.archived)
    db.flush()
    return unit, archived


def delete_unit(
    db: Session, *, course: Course, unit_id: uuid.UUID, archive: bool = False
) -> tuple[SyllabusUnit, bool]:
    unit = _load_unit(db, course=course, unit_id=unit_id)
    archived = False
    if archive:
        unit.archived_at = datetime.now(UTC)
        archived = True
    else:
        allocations = _unit_allocation_count(db, unit.id)
        if allocations:
            raise ConflictError(
                "This unit contains lecture history and cannot be deleted. Archive it instead.",
                code="UNIT_HAS_HISTORY",
                details={
                    "unit_id": str(unit.id),
                    "allocation_count": allocations,
                    "can_archive": True,
                },
            )
        db.delete(unit)
    db.flush()
    return unit, archived


def create_topic(db: Session, *, course: Course, unit_id: uuid.UUID, payload: TopicCreate) -> Topic:
    unit = _load_unit(db, course=course, unit_id=unit_id)
    next_sequence = (
        int(
            db.execute(
                select(func.max(Topic.sequence_number)).where(Topic.unit_id == unit.id)
            ).scalar_one()
            or 0
        )
        + 1
    )
    topic = Topic(
        unit_id=unit.id,
        original_name="",
        title=payload.title.strip(),
        description=payload.description.strip(),
        learning_objectives=list(payload.learning_objectives),
        estimated_duration_minutes=payload.estimated_duration_minutes,
        difficulty=payload.difficulty,
        sequence_number=next_sequence,
        priority=payload.priority,
        is_splittable=payload.is_splittable,
    )
    db.add(topic)
    db.flush()
    _replace_prerequisites(
        db, course=course, topic=topic, prerequisite_ids=payload.prerequisite_ids
    )
    db.flush()
    return topic


def update_topic(
    db: Session, *, course: Course, topic_id: uuid.UUID, payload: TopicUpdate
) -> tuple[Topic, bool]:
    """Apply a partial topic update, including a full prerequisite replacement."""

    topic = _load_topic(db, course=course, topic_id=topic_id)
    data = payload.model_dump(
        exclude_unset=True, exclude={"prerequisite_ids", "archived", "sequence_number"}
    )
    for field, value in data.items():
        setattr(topic, field, value)
    if payload.sequence_number is not None:
        _reposition_topic(db, topic=topic, position=payload.sequence_number)
    archived = _apply_archive_flag(topic, payload.archived)

    if payload.prerequisite_ids is not None:
        _replace_prerequisites(
            db, course=course, topic=topic, prerequisite_ids=payload.prerequisite_ids
        )
    db.flush()
    return topic, archived


def _reposition_topic(db: Session, *, topic: Topic, position: int) -> None:
    """Move ``topic`` to a 1-based position among its unit's topics.

    ``uq_topics_unit_sequence`` is a hard unique constraint, so a bare
    ``sequence_number = position`` collides whenever the target is taken. The
    move therefore parks the siblings on negative numbers first, then writes the
    final contiguous ``1..n`` order.
    """

    siblings = list(
        db.execute(
            select(Topic).where(Topic.unit_id == topic.unit_id).order_by(Topic.sequence_number)
        )
        .scalars()
        .all()
    )
    ordered = [row for row in siblings if row.id != topic.id]
    target = max(1, min(position, len(ordered) + 1)) - 1
    ordered.insert(target, topic)
    for offset, row in enumerate(ordered, start=1):
        row.sequence_number = -offset
    db.flush()
    for final_position, row in enumerate(ordered, start=1):
        row.sequence_number = final_position
    db.flush()


def _replace_prerequisites(
    db: Session,
    *,
    course: Course,
    topic: Topic,
    prerequisite_ids: list[uuid.UUID],
) -> None:
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
    unknown = [item for item in prerequisite_ids if item not in known]
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
    for prerequisite_id in dict.fromkeys(prerequisite_ids):
        if prerequisite_id == topic.id:
            continue
        db.add(
            TopicDependency(
                topic_id=topic.id,
                prerequisite_topic_id=prerequisite_id,
                dependency_type=DependencyType.PREREQUISITE,
            )
        )


def delete_topic(
    db: Session, *, course: Course, topic_id: uuid.UUID, archive: bool = False
) -> tuple[Topic, bool]:
    topic = _load_topic(db, course=course, topic_id=topic_id)
    archived = False
    if archive:
        topic.archived_at = datetime.now(UTC)
        archived = True
    else:
        allocations = _topic_allocation_count(db, topic.id)
        if allocations:
            raise ConflictError(
                "This topic has recorded lecture history and cannot be deleted. "
                "Archive it instead.",
                code="TOPIC_HAS_HISTORY",
                details={
                    "topic_id": str(topic.id),
                    "allocation_count": allocations,
                    "can_archive": True,
                },
            )
        db.delete(topic)
    db.flush()
    return topic, archived


# --------------------------------------------------------------------------- #
# Full-structure save
# --------------------------------------------------------------------------- #


def save_structure(
    db: Session, *, course: Course, payload: SyllabusSaveRequest
) -> tuple[list[SyllabusUnit], list[str]]:
    """Reconcile the editor's full structure without destroying history.

    Existing rows are updated in place. Ids missing from the payload are kept and
    moved behind the saved rows, because their temporary parking order must not
    leak into the editor or the planner. Deletions go through the explicit
    ``DELETE`` endpoints, which enforce the history guard.
    """

    existing_units = load_structure(db, course)
    units_by_id = {unit.id: unit for unit in existing_units}
    topics_by_id = {topic.id: topic for unit in existing_units for topic in unit.topics}

    unknown_units = sorted(
        str(unit.id) for unit in payload.units if unit.id is not None and unit.id not in units_by_id
    )
    if unknown_units:
        raise NotFoundError(
            "A unit in the request does not belong to this course.",
            code="UNIT_NOT_FOUND",
            details={"unit_ids": unknown_units},
        )
    unknown_topics = sorted(
        str(topic.id)
        for unit in payload.units
        for topic in unit.topics
        if topic.id is not None and topic.id not in topics_by_id
    )
    if unknown_topics:
        raise NotFoundError(
            "A topic in the request does not belong to this course.",
            code="TOPIC_NOT_FOUND",
            details={"topic_ids": unknown_topics},
        )

    # Rows the editor omitted are preserved (deletion is explicit), but they are
    # parked with everything else, so their order has to be captured now.
    submitted_topic_ids = {
        topic.id for unit in payload.units for topic in unit.topics if topic.id is not None
    }
    left_over_topics = [
        topic
        for unit in existing_units
        for topic in unit.topics
        if topic.id not in submitted_topic_ids
    ]

    # Phase 1: park every existing row on a unique negative sequence so the
    # (course, sequence) and (unit, sequence) unique constraints cannot collide
    # while the new order is being assigned.
    temporary = 0
    for unit in existing_units:
        temporary -= 1
        unit.sequence_number = temporary
    for topic in topics_by_id.values():
        temporary -= 1
        topic.sequence_number = temporary
    db.flush()

    resolved_units: list[tuple[SyllabusUnit, UnitSave]] = []
    for unit_payload in payload.units:
        if unit_payload.id is not None:
            unit = units_by_id[unit_payload.id]
        else:
            unit = SyllabusUnit(
                course_id=course.id,
                title=unit_payload.title.strip(),
                sequence_number=temporary,
            )
            temporary -= 1
            db.add(unit)
        unit.title = unit_payload.title.strip()
        unit.description = unit_payload.description.strip()
        unit.planned_minutes = unit_payload.planned_minutes
        _apply_archive_flag(unit, unit_payload.archived)
        resolved_units.append((unit, unit_payload))
    db.flush()

    resolved_topics: list[tuple[Topic, TopicSave]] = []
    for unit, unit_payload in resolved_units:
        for topic_payload in unit_payload.topics:
            if topic_payload.id is not None:
                topic = topics_by_id[topic_payload.id]
                topic.unit_id = unit.id
            else:
                topic = Topic(
                    unit_id=unit.id,
                    title=topic_payload.title.strip(),
                    sequence_number=temporary,
                )
                temporary -= 1
                db.add(topic)
            topic.title = topic_payload.title.strip()
            topic.description = topic_payload.description.strip()
            topic.learning_objectives = list(topic_payload.learning_objectives)
            topic.estimated_duration_minutes = topic_payload.estimated_duration_minutes
            topic.difficulty = topic_payload.difficulty
            topic.priority = topic_payload.priority
            topic.is_splittable = topic_payload.is_splittable
            topic.needs_review = topic_payload.needs_review
            _apply_archive_flag(topic, topic_payload.archived)
            resolved_topics.append((topic, topic_payload))
    db.flush()

    # Phase 2: assign the final positive order.
    topic_by_save_id = {id(topic_payload): topic for topic, topic_payload in resolved_topics}
    for order, (unit, unit_payload) in enumerate(resolved_units, start=1):
        unit.sequence_number = order
        for topic_order, topic_payload in enumerate(unit_payload.topics, start=1):
            topic_by_save_id[id(topic_payload)].sequence_number = topic_order
    db.flush()

    # Phase 3: the rows the editor omitted must leave the temporary negative
    # sequence numbers behind. Left negative they sort first in the editor and
    # in the planner, silently jumping ahead of the saved structure.
    resolved_unit_ids = {unit.id for unit, _ in resolved_units}
    next_unit_sequence = len(resolved_units)
    for unit in existing_units:
        if unit.id in resolved_unit_ids:
            continue
        next_unit_sequence += 1
        unit.sequence_number = next_unit_sequence

    next_topic_sequence: dict[uuid.UUID, int] = {
        unit.id: len(unit_payload.topics) for unit, unit_payload in resolved_units
    }
    for topic in left_over_topics:
        position = next_topic_sequence.get(topic.unit_id, 0) + 1
        next_topic_sequence[topic.unit_id] = position
        topic.sequence_number = position
    db.flush()

    for topic, topic_payload in resolved_topics:
        if topic_payload.prerequisite_ids is not None:
            _replace_prerequisites(
                db,
                course=course,
                topic=topic,
                prerequisite_ids=topic_payload.prerequisite_ids,
            )
    db.flush()

    course.version += 1
    db.flush()

    archived_any = any(
        unit_payload.archived is True
        or any(topic_payload.archived is True for topic_payload in unit_payload.topics)
        for unit_payload in payload.units
    )
    return load_structure(db, course), schedule_warnings(db, course, archived=archived_any)


# --------------------------------------------------------------------------- #
# Two-phase ordering
# --------------------------------------------------------------------------- #


def _validate_order(requested: list[uuid.UUID], available: list[uuid.UUID], *, label: str) -> None:
    if len(set(requested)) != len(requested):
        raise ValidationFailedError(
            f"The {label} order contains duplicate ids.", code="INVALID_ORDER"
        )
    missing = sorted(str(item) for item in set(available) - set(requested))
    unknown = sorted(str(item) for item in set(requested) - set(available))
    if missing or unknown:
        raise ValidationFailedError(
            f"The {label} order must list every {label} exactly once.",
            code="INVALID_ORDER",
            details={"missing": missing, "unknown": unknown},
        )


def reorder_units(
    db: Session, *, course: Course, unit_ids: list[uuid.UUID], dry_run: bool = False
) -> tuple[list[SyllabusUnit], list[str]]:
    units = load_structure(db, course)
    by_id = {unit.id: unit for unit in units}
    _validate_order(unit_ids, list(by_id), label="unit")
    if not dry_run:
        # Phase 1: move every row off the contested positive sequence numbers.
        for offset, unit in enumerate(units, start=1):
            unit.sequence_number = -offset
        db.flush()
        # Phase 2: write the requested order.
        for position, unit_id in enumerate(unit_ids, start=1):
            by_id[unit_id].sequence_number = position
        db.flush()
        course.version += 1
        db.flush()
    return [by_id[unit_id] for unit_id in unit_ids], schedule_warnings(db, course)


def reorder_topics(
    db: Session,
    *,
    course: Course,
    unit_id: uuid.UUID,
    topic_ids: list[uuid.UUID],
    dry_run: bool = False,
) -> tuple[list[Topic], list[str]]:
    unit = _load_unit(db, course=course, unit_id=unit_id)
    topics = list(
        db.execute(select(Topic).where(Topic.unit_id == unit.id).order_by(Topic.sequence_number))
        .scalars()
        .all()
    )
    by_id = {topic.id: topic for topic in topics}
    _validate_order(topic_ids, list(by_id), label="topic")
    if not dry_run:
        for offset, topic in enumerate(topics, start=1):
            topic.sequence_number = -offset
        db.flush()
        for position, topic_id in enumerate(topic_ids, start=1):
            by_id[topic_id].sequence_number = position
        db.flush()
        course.version += 1
        db.flush()
    return [by_id[topic_id] for topic_id in topic_ids], schedule_warnings(db, course)

"""Schedule generation, review, approval, and revision history routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CourseDep, CurrentUser, DbSession, Pagination
from app.core.errors import ValidationFailedError
from app.models.entities import ApprovalStatus, RevisionTrigger, ScheduleRevision
from app.schemas.common import Page
from app.schemas.notification import ChangeHistoryEntry
from app.schemas.schedule import (
    ApproveRequest,
    DiffResponse,
    GenerateScheduleRequest,
    RecalculateRequest,
    RevisionChangeRead,
    RevisionSummary,
    RollbackRequest,
    ScheduleRead,
)
from app.services import scheduling as scheduling_service

router = APIRouter(prefix="/courses/{course_id}/schedule", tags=["schedule"])


@router.get("", response_model=ScheduleRead, summary="Current timetable (latest revision)")
def current_schedule(
    course: CourseDep,
    db: DbSession,
    revision_id: Annotated[uuid.UUID | None, Query()] = None,
) -> ScheduleRead:
    revision: ScheduleRevision | None
    if revision_id is not None:
        revision = scheduling_service.get_revision(db, course, revision_id)
    else:
        revision = scheduling_service.base_revision_for(db, course)
    return scheduling_service.build_schedule_read(db, course=course, revision=revision)


@router.post(
    "/generate",
    response_model=ScheduleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Generate the timetable from the confirmed syllabus",
)
def generate(
    course: CourseDep, payload: GenerateScheduleRequest, db: DbSession, user: CurrentUser
) -> ScheduleRead:
    revision = scheduling_service.generate_schedule(
        db,
        course=course,
        user=user,
        trigger=RevisionTrigger.GENERATED,
        strategy=payload.strategy,
    )
    db.commit()
    return scheduling_service.build_schedule_read(db, course=course, revision=revision)


@router.post(
    "/recalculate",
    response_model=ScheduleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Recalculate the remaining schedule (creates a revision to review)",
)
def recalculate(
    course: CourseDep, payload: RecalculateRequest, db: DbSession, user: CurrentUser
) -> ScheduleRead:
    revision = scheduling_service.generate_schedule(
        db,
        course=course,
        user=user,
        trigger=payload.trigger,
        strategy=payload.strategy,
        reason=payload.reason,
        base_revision_id=payload.base_revision_id,
    )
    db.commit()
    return scheduling_service.build_schedule_read(db, course=course, revision=revision)


@router.get("/revisions", response_model=Page[RevisionSummary], summary="Revision history")
def list_revisions(
    course: CourseDep,
    db: DbSession,
    pagination: Pagination,
    approval_status: Annotated[ApprovalStatus | None, Query()] = None,
) -> Page[RevisionSummary]:
    from sqlalchemy import func, select

    from app.models.entities import ScheduleRevision

    limit, offset = pagination
    conditions = [ScheduleRevision.course_id == course.id]
    if approval_status is not None:
        conditions.append(ScheduleRevision.approval_status == approval_status)
    total = int(
        db.execute(
            select(func.count()).select_from(ScheduleRevision).where(*conditions)
        ).scalar_one()
    )
    rows = (
        db.execute(
            select(ScheduleRevision)
            .where(*conditions)
            .order_by(ScheduleRevision.revision_number.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return Page.build(
        [scheduling_service.revision_summary(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/revisions/{revision_id}",
    response_model=ScheduleRead,
    summary="A single revision with its lectures and changes",
)
def get_revision(revision_id: uuid.UUID, course: CourseDep, db: DbSession) -> ScheduleRead:
    revision = scheduling_service.get_revision(db, course, revision_id)
    return scheduling_service.build_schedule_read(db, course=course, revision=revision)


@router.get(
    "/revisions/{revision_id}/diff",
    response_model=DiffResponse,
    summary="Before/after comparison for a proposed revision",
)
def revision_diff(revision_id: uuid.UUID, course: CourseDep, db: DbSession) -> DiffResponse:
    revision = scheduling_service.get_revision(db, course, revision_id)
    base = (
        scheduling_service.get_revision(db, course, revision.base_revision_id)
        if revision.base_revision_id
        else None
    )
    explanation = revision.explanation or {}
    from app.schemas.schedule import DiffSummary

    diff = DiffSummary.model_validate(explanation.get("diff") or {})
    return DiffResponse(
        revision=scheduling_service.revision_summary(revision),
        base_revision=scheduling_service.revision_summary(base) if base else None,
        diff=diff,
    )


@router.get(
    "/revisions/{revision_id}/changes",
    response_model=list[RevisionChangeRead],
    summary="Line-level change ledger for a revision",
)
def revision_changes(
    revision_id: uuid.UUID, course: CourseDep, db: DbSession
) -> list[RevisionChangeRead]:
    from sqlalchemy import select

    from app.models.entities import ScheduleRevisionChange

    scheduling_service.get_revision(db, course, revision_id)
    rows = (
        db.execute(
            select(ScheduleRevisionChange)
            .where(ScheduleRevisionChange.revision_id == revision_id)
            .order_by(ScheduleRevisionChange.new_date, ScheduleRevisionChange.change_type)
        )
        .scalars()
        .all()
    )
    return [RevisionChangeRead.model_validate(row) for row in rows]


@router.post(
    "/revisions/{revision_id}/approve",
    response_model=ScheduleRead,
    summary="Approve a revision (and notify) — required before it is published",
)
def approve(
    revision_id: uuid.UUID,
    course: CourseDep,
    payload: ApproveRequest,
    db: DbSession,
    user: CurrentUser,
) -> ScheduleRead:
    revision = scheduling_service.get_revision(db, course, revision_id)
    approved = scheduling_service.approve_revision(
        db, course=course, revision=revision, user=user, note=payload.note, publish=payload.publish
    )
    db.commit()
    return scheduling_service.build_schedule_read(db, course=course, revision=approved)


@router.post(
    "/revisions/{revision_id}/reject",
    response_model=RevisionSummary,
    summary="Reject a proposed revision",
)
def reject(
    revision_id: uuid.UUID,
    course: CourseDep,
    payload: ApproveRequest,
    db: DbSession,
    user: CurrentUser,
) -> RevisionSummary:
    revision = scheduling_service.get_revision(db, course, revision_id)
    rejected = scheduling_service.reject_revision(
        db, course=course, revision=revision, user=user, reason=payload.note
    )
    db.commit()
    return scheduling_service.revision_summary(rejected)


@router.post(
    "/revisions/{revision_id}/rollback",
    response_model=ScheduleRead,
    summary="Restore an earlier revision as the current timetable",
)
def rollback(
    revision_id: uuid.UUID,
    course: CourseDep,
    payload: RollbackRequest,
    db: DbSession,
    user: CurrentUser,
) -> ScheduleRead:
    revision = scheduling_service.get_revision(db, course, revision_id)
    if revision.approval_status not in {ApprovalStatus.APPROVED, ApprovalStatus.SUPERSEDED}:
        raise ValidationFailedError(
            "Only an approved revision can be restored.", code="REVISION_NOT_RESTORABLE"
        )
    restored = scheduling_service.rollback_to_revision(
        db, course=course, revision=revision, user=user, reason=payload.reason
    )
    db.commit()
    return scheduling_service.build_schedule_read(db, course=course, revision=restored)


@router.get(
    "/history",
    response_model=Page[ChangeHistoryEntry],
    summary="What changed, when, and why",
)
def change_history(
    course: CourseDep, db: DbSession, pagination: Pagination
) -> Page[ChangeHistoryEntry]:
    from sqlalchemy import func, select

    from app.models.entities import ScheduleRevision, ScheduleRevisionChange

    limit, offset = pagination
    total = int(
        db.execute(
            select(func.count())
            .select_from(ScheduleRevision)
            .where(ScheduleRevision.course_id == course.id)
        ).scalar_one()
    )
    rows = (
        db.execute(
            select(ScheduleRevision)
            .where(ScheduleRevision.course_id == course.id)
            .order_by(ScheduleRevision.revision_number.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    entries: list[ChangeHistoryEntry] = []
    for revision in rows:
        change_count = int(
            db.execute(
                select(func.count())
                .select_from(ScheduleRevisionChange)
                .where(ScheduleRevisionChange.revision_id == revision.id)
            ).scalar_one()
        )
        entries.append(
            ChangeHistoryEntry(
                revision_id=revision.id,
                revision_number=revision.revision_number,
                trigger_type=str(revision.trigger_type),
                approval_status=str(revision.approval_status),
                change_summary=revision.change_summary,
                warnings=list(revision.warnings or []),
                change_count=change_count,
                created_at=revision.created_at,
                approved_at=revision.approved_at,
                published_at=revision.published_at,
                explanation=dict(revision.explanation or {}),
            )
        )
    return Page.build(entries, total=total, limit=limit, offset=offset)

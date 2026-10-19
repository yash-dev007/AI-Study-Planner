"""Audit logging and in-app notifications.

Both are deliberately small: an audit row per meaningful mutation and a
notification row per message the professor should see. Notifications are
created only for approved/published schedules, so a draft proposal never
notifies anyone.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import AuditLog, Notification, NotificationType


def record_audit(
    db: Session,
    *,
    user_id: uuid.UUID | None,
    action: str,
    course_id: uuid.UUID | None = None,
    entity_type: str = "",
    entity_id: str = "",
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        user_id=user_id,
        course_id=course_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        detail=detail or {},
    )
    db.add(entry)
    return entry


def notify(
    db: Session,
    *,
    user_id: uuid.UUID,
    event_type: NotificationType,
    message: str,
    course_id: uuid.UUID | None = None,
    revision_id: uuid.UUID | None = None,
) -> Notification:
    notification = Notification(
        user_id=user_id,
        course_id=course_id,
        revision_id=revision_id,
        event_type=event_type,
        message=message,
    )
    db.add(notification)
    return notification


def unread_count(db: Session, user_id: uuid.UUID) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        ).scalar_one()
    )


def mark_read(db: Session, notification: Notification) -> Notification:
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
    return notification

"""In-app notification routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError
from app.models.entities import Notification
from app.schemas.notification import (
    NotificationListResponse,
    NotificationRead,
    NotificationUpdateRequest,
)
from app.services.audit import mark_read, unread_count

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse, summary="My notifications")
def list_notifications(
    db: DbSession, user: CurrentUser, unread_only: bool = False, limit: int = 50
) -> NotificationListResponse:
    query = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    rows = (
        db.execute(query.order_by(Notification.created_at.desc()).limit(max(1, min(limit, 200))))
        .scalars()
        .all()
    )
    return NotificationListResponse(
        items=[NotificationRead.model_validate(row) for row in rows],
        unread_count=unread_count(db, user.id),
    )


@router.patch(
    "/{notification_id}",
    response_model=NotificationRead,
    summary="Mark a notification as read or unread",
)
def update_notification(
    notification_id: uuid.UUID,
    payload: NotificationUpdateRequest,
    db: DbSession,
    user: CurrentUser,
) -> NotificationRead:
    notification = db.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise NotFoundError("Notification not found.", code="NOTIFICATION_NOT_FOUND")
    if payload.read:
        mark_read(db, notification)
    else:
        notification.read_at = None
    db.commit()
    return NotificationRead.model_validate(notification)


@router.post("/read-all", response_model=NotificationListResponse, summary="Mark everything read")
def read_all(db: DbSession, user: CurrentUser) -> NotificationListResponse:
    rows = (
        db.execute(
            select(Notification).where(
                Notification.user_id == user.id, Notification.read_at.is_(None)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        mark_read(db, row)
    db.commit()
    return NotificationListResponse(
        items=[NotificationRead.model_validate(row) for row in rows],
        unread_count=unread_count(db, user.id),
    )

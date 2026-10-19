"""Notification schemas. In-app delivery first; other channels stay pluggable."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.entities import NotificationType


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    course_id: uuid.UUID | None = None
    revision_id: uuid.UUID | None = None
    event_type: NotificationType
    message: str
    read_at: datetime | None = None
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationRead]
    unread_count: int


class NotificationUpdateRequest(BaseModel):
    read: bool = True


class ChangeHistoryEntry(BaseModel):
    revision_id: uuid.UUID
    revision_number: int
    trigger_type: str
    approval_status: str
    change_summary: str
    warnings: list[str] = Field(default_factory=list)
    change_count: int = 0
    created_at: datetime
    approved_at: datetime | None = None
    published_at: datetime | None = None
    explanation: dict[str, object] = Field(default_factory=dict)

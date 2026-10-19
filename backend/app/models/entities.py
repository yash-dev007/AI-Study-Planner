"""Database models.

The schema keeps course configuration, syllabus structure, generated sessions,
allocations, and immutable schedule revisions as separate concerns. Schedule
revisions are append-only snapshots: approving a revision never mutates the
allocations belonging to a prior revision.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class StringEnum(StrEnum):
    """String enum base: stored by name in the database, serialized by value."""


class UserRole(StringEnum):
    PROFESSOR = "professor"
    ADMIN = "admin"


class CourseStatus(StringEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class DocumentStatus(StringEnum):
    UPLOADED = "uploaded"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class SessionStatus(StringEnum):
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    MISSED = "missed"
    CANCELLED = "cancelled"


class AllocationStatus(StringEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class RevisionTrigger(StringEnum):
    GENERATED = "generated"
    MANUAL_EDIT = "manual_edit"
    LECTURE_MISSED = "lecture_missed"
    LECTURE_COMPLETED = "lecture_completed"
    COURSE_CONFIG_CHANGED = "course_config_changed"
    ROLLBACK = "rollback"


class ApprovalStatus(StringEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ExceptionType(StringEnum):
    HOLIDAY = "holiday"
    EXAM = "exam"
    EVENT = "event"
    UNAVAILABLE = "unavailable"


class DependencyType(StringEnum):
    PREREQUISITE = "prerequisite"
    RECOMMENDED = "recommended"


class NotificationType(StringEnum):
    SCHEDULE_APPROVED = "schedule_approved"
    SCHEDULE_PENDING = "schedule_pending"
    LECTURE_REMINDER = "lecture_reminder"
    CAPACITY_WARNING = "capacity_warning"
    SYLLABUS_PROCESSED = "syllabus_processed"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, length=20), default=UserRole.PROFESSOR, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    courses: Mapped[list[Course]] = relationship(back_populates="professor")


class Course(TimestampMixin, Base):
    __tablename__ = "courses"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_courses_dates"),
        CheckConstraint("total_weeks > 0", name="ck_courses_total_weeks"),
        Index("ix_courses_professor_status", "professor_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    course_code: Mapped[str] = mapped_column(String(50), nullable=False)
    professor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department: Mapped[str] = mapped_column(String(150), nullable=False, default="")
    semester: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    academic_year: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    status: Mapped[CourseStatus] = mapped_column(
        Enum(CourseStatus, native_enum=False, length=20), default=CourseStatus.DRAFT, nullable=False
    )
    lecture_duration_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    total_weeks: Mapped[int] = mapped_column(Integer, default=16, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    professor: Mapped[User] = relationship(back_populates="courses")
    units: Mapped[list[SyllabusUnit]] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
        order_by="SyllabusUnit.sequence_number",
    )
    availability_rules: Mapped[list[AvailabilityRule]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    exceptions: Mapped[list[CalendarException]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[LectureSession]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    revisions: Mapped[list[ScheduleRevision]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )


class SyllabusDocument(TimestampMixin, Base):
    __tablename__ = "syllabus_documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    processing_status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, native_enum=False, length=30),
        default=DocumentStatus.UPLOADED,
        nullable=False,
    )
    extracted_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    extraction_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    course: Mapped[Course] = relationship()


class SyllabusUnit(TimestampMixin, Base):
    __tablename__ = "syllabus_units"
    __table_args__ = (
        UniqueConstraint("course_id", "sequence_number", name="uq_units_course_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)

    course: Mapped[Course] = relationship(back_populates="units")
    topics: Mapped[list[Topic]] = relationship(
        back_populates="unit", cascade="all, delete-orphan", order_by="Topic.sequence_number"
    )


class Topic(TimestampMixin, Base):
    __tablename__ = "topics"
    __table_args__ = (
        UniqueConstraint("unit_id", "sequence_number", name="uq_topics_unit_sequence"),
        CheckConstraint("estimated_duration_minutes > 0", name="ck_topics_duration_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("syllabus_units.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    learning_objectives: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    estimated_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    difficulty: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    is_splittable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    unit: Mapped[SyllabusUnit] = relationship(back_populates="topics")
    allocations: Mapped[list[TopicAllocation]] = relationship(
        back_populates="topic", cascade="all, delete-orphan"
    )


class TopicDependency(Base):
    __tablename__ = "topic_dependencies"
    __table_args__ = (
        UniqueConstraint("topic_id", "prerequisite_topic_id", name="uq_topic_dependency"),
        CheckConstraint("topic_id <> prerequisite_topic_id", name="ck_dependency_not_self"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prerequisite_topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dependency_type: Mapped[DependencyType] = mapped_column(
        Enum(DependencyType, native_enum=False, length=20),
        default=DependencyType.PREREQUISITE,
        nullable=False,
    )


class AvailabilityRule(TimestampMixin, Base):
    __tablename__ = "availability_rules"
    __table_args__ = (
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_availability_weekday"),
        CheckConstraint("end_time > start_time", name="ck_availability_time_order"),
        UniqueConstraint(
            "course_id", "weekday", "start_time", "end_time", name="uq_availability_slot"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    room: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_until: Mapped[date | None] = mapped_column(Date)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    course: Mapped[Course] = relationship(back_populates="availability_rules")


class CalendarException(TimestampMixin, Base):
    __tablename__ = "calendar_exceptions"
    __table_args__ = (
        UniqueConstraint("course_id", "date", "exception_type", name="uq_calendar_exception"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    exception_type: Mapped[ExceptionType] = mapped_column(
        Enum(ExceptionType, native_enum=False, length=20), nullable=False
    )
    description: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    blocks_teaching: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    course: Mapped[Course] = relationship(back_populates="exceptions")


class ScheduleRevision(TimestampMixin, Base):
    __tablename__ = "schedule_revisions"
    __table_args__ = (
        UniqueConstraint("course_id", "revision_number", name="uq_revision_number"),
        Index("ix_revisions_course_status", "course_id", "approval_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_type: Mapped[RevisionTrigger] = mapped_column(
        Enum(RevisionTrigger, native_enum=False, length=40), nullable=False
    )
    # Audit reference to the lecture that caused this revision. It deliberately
    # carries no foreign key: lecture_sessions already references
    # schedule_revisions, and a second constraint would form a cycle that breaks
    # table creation order and migrations.
    trigger_lecture_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    change_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, native_enum=False, length=30),
        default=ApprovalStatus.DRAFT,
        nullable=False,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    base_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedule_revisions.id", ondelete="SET NULL")
    )

    course: Mapped[Course] = relationship(back_populates="revisions")
    sessions: Mapped[list[LectureSession]] = relationship(
        back_populates="revision",
        cascade="all, delete-orphan",
        order_by="LectureSession.sequence",
        # A revision also points at the lecture that triggered it, so the join
        # column has to be named explicitly.
        foreign_keys="LectureSession.revision_id",
    )
    changes: Mapped[list[ScheduleRevisionChange]] = relationship(
        back_populates="revision", cascade="all, delete-orphan"
    )


class LectureSession(TimestampMixin, Base):
    __tablename__ = "lecture_sessions"
    __table_args__ = (
        UniqueConstraint("revision_id", "sequence", name="uq_session_revision_sequence"),
        CheckConstraint("scheduled_end > scheduled_start", name="ck_session_time_order"),
        Index("ix_sessions_course_start", "course_id", "scheduled_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("schedule_revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, native_enum=False, length=20),
        default=SessionStatus.SCHEDULED,
        nullable=False,
    )
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    carry_over_from_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lecture_sessions.id", ondelete="SET NULL")
    )

    course: Mapped[Course] = relationship(back_populates="sessions")
    revision: Mapped[ScheduleRevision] = relationship(
        back_populates="sessions", foreign_keys="LectureSession.revision_id"
    )
    allocations: Mapped[list[TopicAllocation]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class TopicAllocation(TimestampMixin, Base):
    __tablename__ = "topic_allocations"
    __table_args__ = (
        UniqueConstraint("lecture_session_id", "topic_id", name="uq_allocation_session_topic"),
        CheckConstraint("allocated_duration_minutes > 0", name="ck_allocation_duration_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    lecture_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lecture_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    allocated_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_status: Mapped[AllocationStatus] = mapped_column(
        Enum(AllocationStatus, native_enum=False, length=20),
        default=AllocationStatus.PLANNED,
        nullable=False,
    )
    actual_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    sequence_in_session: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    session: Mapped[LectureSession] = relationship(back_populates="allocations")
    topic: Mapped[Topic] = relationship(back_populates="allocations")


class ScheduleRevisionChange(Base):
    __tablename__ = "schedule_revision_changes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("schedule_revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lecture_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lecture_sessions.id", ondelete="SET NULL")
    )
    topic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"))
    change_type: Mapped[str] = mapped_column(String(40), nullable=False)
    previous_date: Mapped[date | None] = mapped_column(Date)
    new_date: Mapped[date | None] = mapped_column(Date)
    previous_minutes: Mapped[int | None] = mapped_column(Integer)
    new_minutes: Mapped[int | None] = mapped_column(Integer)
    change_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)

    revision: Mapped[ScheduleRevision] = relationship(back_populates="changes")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_read", "user_id", "read_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedule_revisions.id", ondelete="SET NULL")
    )
    event_type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, native_enum=False, length=40), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

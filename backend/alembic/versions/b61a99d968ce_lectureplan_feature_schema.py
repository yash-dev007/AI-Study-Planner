"""lectureplan feature schema

Adds teacher preferences, AI suggestions, email delivery tracking, lecture
provenance, and allocation-ledger fields. Durations stay integer minutes.

Revision ID: b61a99d968ce
Revises: 8279cdca4786
Create Date: 2026-10-19 19:08:48.154263
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b61a99d968ce"
down_revision: str | None = "8279cdca4786"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- users -------------------------------------------------------------
    op.add_column(
        "users",
        sa.Column("department", sa.String(length=150), server_default="", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("college_name", sa.String(length=200), server_default="", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), server_default="0", nullable=False),
    )

    # --- courses -----------------------------------------------------------
    with op.batch_alter_table("courses") as batch_op:
        batch_op.alter_column(
            "course_code", existing_type=sa.String(length=50), nullable=True
        )
        batch_op.add_column(sa.Column("target_completion_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("total_available_minutes", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "ck_courses_total_available_positive",
            "total_available_minutes IS NULL OR total_available_minutes > 0",
        )

    # --- syllabus units ----------------------------------------------------
    op.add_column("syllabus_units", sa.Column("planned_minutes", sa.Integer(), nullable=True))
    op.add_column(
        "syllabus_units", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )

    # --- topics ------------------------------------------------------------
    with op.batch_alter_table("topics") as batch_op:
        batch_op.add_column(
            sa.Column("original_name", sa.String(length=300), server_default="", nullable=False)
        )
        batch_op.add_column(
            sa.Column(
                "analysis_status",
                sa.String(length=30),
                server_default="NOT_ANALYZED",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("analysis_approved_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("duration_locked", sa.Boolean(), server_default="0", nullable=False)
        )
        batch_op.create_check_constraint(
            "ck_topics_priority_range", "priority >= 1 AND priority <= 5"
        )
        batch_op.create_check_constraint(
            "ck_topics_difficulty_value", "difficulty IN ('easy', 'medium', 'hard')"
        )

    # --- lecture sessions --------------------------------------------------
    with op.batch_alter_table("lecture_sessions") as batch_op:
        batch_op.add_column(
            sa.Column("source", sa.String(length=20), server_default="PLANNED", nullable=False)
        )
        batch_op.add_column(sa.Column("rescheduled_from_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=120), nullable=True))
        batch_op.create_foreign_key(
            "fk_sessions_rescheduled_from",
            "lecture_sessions",
            ["rescheduled_from_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_sessions_course_idempotency", ["course_id", "idempotency_key"]
        )

    # --- topic allocations: align names with the ledger contract -----------
    with op.batch_alter_table("topic_allocations") as batch_op:
        # The reflected check still names the old column; replace it explicitly.
        batch_op.drop_constraint("ck_allocation_duration_positive", type_="check")
        batch_op.alter_column(
            "allocated_duration_minutes",
            new_column_name="planned_minutes",
            existing_type=sa.Integer(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "actual_duration_minutes",
            new_column_name="actual_minutes",
            existing_type=sa.Integer(),
            existing_nullable=True,
        )
        batch_op.create_check_constraint(
            "ck_allocation_duration_positive", "planned_minutes > 0"
        )
        batch_op.create_check_constraint(
            "ck_allocation_actual_non_negative",
            "actual_minutes IS NULL OR actual_minutes >= 0",
        )
        batch_op.create_check_constraint(
            "ck_allocation_actual_within_planned",
            "actual_minutes IS NULL OR actual_minutes <= planned_minutes",
        )

    # --- new tables --------------------------------------------------------
    op.create_table(
        "user_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "default_lecture_minutes", sa.Integer(), server_default="60", nullable=False
        ),
        sa.Column("default_teaching_days", sa.JSON(), nullable=False),
        sa.Column(
            "weekly_email_enabled", sa.Boolean(), server_default="0", nullable=False
        ),
        sa.Column("email_weekday", sa.Integer(), server_default="0", nullable=False),
        sa.Column("email_hour_local", sa.Integer(), server_default="8", nullable=False),
        sa.Column("ai_enabled", sa.Boolean(), server_default="0", nullable=False),
        sa.Column(
            "preferred_ai_provider",
            sa.String(length=20),
            server_default="none",
            nullable=False,
        ),
        sa.Column(
            "preferred_ai_model", sa.String(length=120), server_default="", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "default_lecture_minutes > 0", name="ck_user_settings_lecture_minutes"
        ),
        sa.CheckConstraint(
            "email_weekday >= 0 AND email_weekday <= 6",
            name="ck_user_settings_email_weekday",
        ),
        sa.CheckConstraint(
            "email_hour_local >= 0 AND email_hour_local <= 23",
            name="ck_user_settings_email_hour",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    with op.batch_alter_table("user_settings", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_user_settings_user_id"), ["user_id"], unique=True
        )

    op.create_table(
        "topic_suggestions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column(
            "suggested_title", sa.String(length=300), server_default="", nullable=False
        ),
        sa.Column(
            "suggested_difficulty",
            sa.String(length=20),
            server_default="medium",
            nullable=False,
        ),
        sa.Column("suggested_priority", sa.Integer(), server_default="3", nullable=False),
        sa.Column("suggested_minutes", sa.Integer(), server_default="60", nullable=False),
        sa.Column("prerequisite_keys", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), server_default="", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("provider", sa.String(length=40), server_default="", nullable=False),
        sa.Column("model", sa.String(length=120), server_default="", nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "suggested_priority >= 1 AND suggested_priority <= 5",
            name="ck_topic_suggestions_priority_range",
        ),
        sa.CheckConstraint(
            "suggested_minutes >= 15 AND suggested_minutes <= 480",
            name="ck_topic_suggestions_minutes_range",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_topic_suggestions_confidence_range",
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_id", name="uq_topic_suggestions_topic"),
    )
    with op.batch_alter_table("topic_suggestions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_topic_suggestions_topic_id"), ["topic_id"], unique=True
        )

    op.create_table(
        "email_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column(
            "kind", sa.String(length=40), server_default="weekly_summary", nullable=False
        ),
        sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "period_start", "period_end", "kind", name="uq_email_delivery_period"
        ),
    )
    with op.batch_alter_table("email_deliveries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_email_deliveries_user_id"), ["user_id"], unique=False
        )


def downgrade() -> None:
    op.drop_table("email_deliveries")
    op.drop_table("topic_suggestions")
    op.drop_table("user_settings")

    with op.batch_alter_table("topic_allocations") as batch_op:
        batch_op.drop_constraint(
            "ck_allocation_actual_within_planned", type_="check"
        )
        batch_op.drop_constraint("ck_allocation_actual_non_negative", type_="check")
        batch_op.drop_constraint("ck_allocation_duration_positive", type_="check")
        batch_op.alter_column(
            "planned_minutes",
            new_column_name="allocated_duration_minutes",
            existing_type=sa.Integer(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "actual_minutes",
            new_column_name="actual_duration_minutes",
            existing_type=sa.Integer(),
            existing_nullable=True,
        )
        batch_op.create_check_constraint(
            "ck_allocation_duration_positive", "allocated_duration_minutes > 0"
        )

    with op.batch_alter_table("lecture_sessions") as batch_op:
        batch_op.drop_constraint("uq_sessions_course_idempotency", type_="unique")
        batch_op.drop_constraint("fk_sessions_rescheduled_from", type_="foreignkey")
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("rescheduled_from_id")
        batch_op.drop_column("source")

    with op.batch_alter_table("topics") as batch_op:
        batch_op.drop_constraint("ck_topics_difficulty_value", type_="check")
        batch_op.drop_constraint("ck_topics_priority_range", type_="check")
        batch_op.drop_column("duration_locked")
        batch_op.drop_column("archived_at")
        batch_op.drop_column("analysis_approved_at")
        batch_op.drop_column("analysis_status")
        batch_op.drop_column("original_name")

    op.drop_column("syllabus_units", "archived_at")
    op.drop_column("syllabus_units", "planned_minutes")

    with op.batch_alter_table("courses") as batch_op:
        batch_op.drop_constraint("ck_courses_total_available_positive", type_="check")
        batch_op.drop_column("total_available_minutes")
        batch_op.drop_column("target_completion_date")
        # A downgrade must not fail on rows created without a course code.
        op.execute("UPDATE courses SET course_code = '' WHERE course_code IS NULL")
        batch_op.alter_column(
            "course_code", existing_type=sa.String(length=50), nullable=False
        )

    op.drop_column("users", "token_version")
    op.drop_column("users", "timezone")
    op.drop_column("users", "college_name")
    op.drop_column("users", "department")

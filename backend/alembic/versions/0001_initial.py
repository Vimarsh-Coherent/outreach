"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-29
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "outreach"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        schema=SCHEMA,
    )

    op.create_table(
        "leads",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("identity_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("email", sa.String(254)),
        sa.Column("phone", sa.String(32)),
        sa.Column("linkedin_url", sa.String(500)),
        sa.Column("first_name", sa.String(120)),
        sa.Column("last_name", sa.String(120)),
        sa.Column("company", sa.String(200)),
        sa.Column("title", sa.String(200)),
        sa.Column("custom", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source", sa.String(40)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("user_id", "identity_hash", name="uq_leads_user_identity"),
        schema=SCHEMA,
    )
    op.create_index("ix_leads_user", "leads", ["user_id"], schema=SCHEMA)
    op.create_index("ix_leads_email", "leads", ["user_id", "email"], schema=SCHEMA)

    op.create_table(
        "channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel_type", sa.String(20), nullable=False),
        sa.Column("display_label", sa.String(120), nullable=False),
        sa.Column("config_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("daily_cap", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("sent_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_today_window_start", sa.DateTime(timezone=True)),
        sa.Column("ext_last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint("channel_type IN ('email','linkedin')", name="ck_channels_type"),
        sa.CheckConstraint("status IN ('active','paused','invalid')", name="ck_channels_status"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_channels_user_type", "channels", ["user_id", "channel_type", "status"], schema=SCHEMA
    )

    op.create_table(
        "sequences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("send_window_start", sa.Time(), nullable=False, server_default="09:00:00"),
        sa.Column("send_window_end", sa.Time(), nullable=False, server_default="18:00:00"),
        sa.Column("send_days_mask", sa.SmallInteger(), nullable=False, server_default="31"),
        sa.Column("ai_followups_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint("status IN ('draft','active','paused','archived')", name="ck_sequences_status"),
        sa.CheckConstraint("send_window_start < send_window_end", name="ck_sequences_window"),
        sa.CheckConstraint("send_days_mask BETWEEN 1 AND 127", name="ck_sequences_days"),
        schema=SCHEMA,
    )
    op.create_index("ix_sequences_user_status", "sequences", ["user_id", "status"], schema=SCHEMA)

    op.create_table(
        "sequence_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "sequence_id", sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.sequences.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("step_order", sa.SmallInteger(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("delay_days", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("delay_hours", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("subject", sa.Text()),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("sequence_id", "step_order", name="uq_steps_order"),
        sa.CheckConstraint(
            "channel IN ('email','linkedin_dm','linkedin_connect')", name="ck_steps_channel"
        ),
        sa.CheckConstraint("delay_days BETWEEN 0 AND 365", name="ck_steps_delay_days"),
        sa.CheckConstraint("delay_hours BETWEEN 0 AND 23", name="ck_steps_delay_hours"),
        schema=SCHEMA,
    )

    op.create_table(
        "enrolments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "sequence_id", sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.sequences.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "lead_id", sa.BigInteger(),
            sa.ForeignKey(f"{SCHEMA}.leads.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("identity_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("contact_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("current_step_order", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("next_send_at", sa.DateTime(timezone=True)),
        sa.Column("runtime_state", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.Column("stopped_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','paused','done','stopped_reply','stopped_bounce',"
            "'stopped_manual','stopped_archived','errored')",
            name="ck_enrolments_status",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_enrol_sequence", "enrolments", ["sequence_id", "status"], schema=SCHEMA)
    op.create_index("ix_enrol_user", "enrolments", ["user_id", "status"], schema=SCHEMA)
    op.create_index(
        "ix_enrol_due", "enrolments", ["next_send_at"], schema=SCHEMA,
        postgresql_where=sa.text("status = 'active' AND next_send_at IS NOT NULL"),
    )
    op.create_index(
        "uq_enrol_active_identity", "enrolments", ["sequence_id", "identity_hash"],
        unique=True, schema=SCHEMA,
        postgresql_where=sa.text("status IN ('active','paused')"),
    )

    op.create_table(
        "step_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "enrolment_id", sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.enrolments.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("step_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.sequence_steps.id"), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("provider_message_id", sa.String(200)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','reserving','sent','failed','bounced','queued_external','skipped')",
            name="ck_runs_status",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_runs_enrolment_sent", "step_runs", ["enrolment_id", "sent_at"], schema=SCHEMA,
        postgresql_where=sa.text("status = 'sent'"),
    )
    op.create_index(
        "ix_runs_provider", "step_runs", ["provider_message_id"], schema=SCHEMA,
        postgresql_where=sa.text("provider_message_id IS NOT NULL"),
    )

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "enrolment_id", sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.enrolments.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("step_run_id", sa.BigInteger(), sa.ForeignKey(f"{SCHEMA}.step_runs.id")),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(200)),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('reply','delivered','bounce','auto_reply','open','click',"
            "'connection_accepted','dm_delivered','error')",
            name="ck_events_type",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_events_enrolment", "events", ["enrolment_id", "occurred_at"], schema=SCHEMA)
    op.create_index(
        "uq_events_external", "events", ["enrolment_id", "event_type", "external_id"],
        unique=True, schema=SCHEMA,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    op.create_table(
        "reply_sentiment",
        sa.Column(
            "event_id", sa.BigInteger(),
            sa.ForeignKey(f"{SCHEMA}.events.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("label", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reasoning", sa.String(500)),
        sa.Column("model", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "label IN ('positive','interested','objection','negative','unsubscribe',"
            "'auto_reply','neutral')",
            name="ck_sentiment_label",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_sentiment_label", "reply_sentiment", ["label"], schema=SCHEMA)

    op.create_table(
        "li_commands",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "enrolment_id", sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.enrolments.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("step_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.sequence_steps.id"), nullable=False),
        sa.Column("command_type", sa.String(20), nullable=False),
        sa.Column("target_li_url", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint("command_type IN ('dm','connect','view_profile')", name="ck_licmd_type"),
        sa.CheckConstraint("status IN ('pending','claimed','done','failed','expired')", name="ck_licmd_status"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_licmd_pending", "li_commands", ["user_id", "created_at"], schema=SCHEMA,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "suppressions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("identity_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("user_id", "identity_hash", "channel", name="uq_suppress_user_id_chan"),
        schema=SCHEMA,
    )

    op.create_table(
        "vault_index",
        sa.Column(
            "lead_id", sa.BigInteger(),
            sa.ForeignKey(f"{SCHEMA}.leads.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("vault_name", sa.String(120), nullable=False),
        sa.Column("indexed_event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True)),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for tbl in (
        "vault_index", "suppressions", "li_commands", "reply_sentiment",
        "events", "step_runs", "enrolments", "sequence_steps", "sequences",
        "channels", "leads", "users",
    ):
        op.drop_table(tbl, schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")

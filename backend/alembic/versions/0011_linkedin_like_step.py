"""linkedin_like step type + like_posts command type

Adds:
  - 'linkedin_like' to sequence_steps.channel check constraint
  - 'like_posts' to li_commands.command_type check constraint

Revision ID: 0011
Revises: 0010
"""
from collections.abc import Sequence as SequenceType

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: SequenceType[str] | None = None
depends_on: SequenceType[str] | None = None

SCHEMA = "outreach"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.sequence_steps DROP CONSTRAINT IF EXISTS ck_steps_channel")
    op.execute(
        f"ALTER TABLE {SCHEMA}.sequence_steps ADD CONSTRAINT ck_steps_channel "
        "CHECK (channel IN ('email','linkedin_dm','linkedin_connect','call','sms','whatsapp','linkedin_like'))"
    )
    op.execute(f"ALTER TABLE {SCHEMA}.li_commands DROP CONSTRAINT IF EXISTS ck_licmd_type")
    op.execute(
        f"ALTER TABLE {SCHEMA}.li_commands ADD CONSTRAINT ck_licmd_type "
        "CHECK (command_type IN ('dm','connect','view_profile','like_posts'))"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.sequence_steps DROP CONSTRAINT IF EXISTS ck_steps_channel")
    op.execute(
        f"ALTER TABLE {SCHEMA}.sequence_steps ADD CONSTRAINT ck_steps_channel "
        "CHECK (channel IN ('email','linkedin_dm','linkedin_connect','call','sms','whatsapp'))"
    )
    op.execute(f"ALTER TABLE {SCHEMA}.li_commands DROP CONSTRAINT IF EXISTS ck_licmd_type")
    op.execute(
        f"ALTER TABLE {SCHEMA}.li_commands ADD CONSTRAINT ck_licmd_type "
        "CHECK (command_type IN ('dm','connect','view_profile'))"
    )

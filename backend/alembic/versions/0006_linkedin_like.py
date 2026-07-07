"""linkedin post-like step + command type

Adds 'linkedin_like' to the sequence_steps.channel CHECK constraint and
'like' to the li_commands.command_type CHECK constraint, so a sequence step
can drive the extension to like a lead's most recent LinkedIn post.

Revision ID: 0006
Revises: 0005
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "outreach"


def upgrade() -> None:
    op.drop_constraint("ck_steps_channel", "sequence_steps", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_steps_channel",
        "sequence_steps",
        "channel IN ('email','linkedin_dm','linkedin_connect','linkedin_like','call','sms','whatsapp')",
        schema=SCHEMA,
    )
    op.drop_constraint("ck_licmd_type", "li_commands", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_licmd_type",
        "li_commands",
        "command_type IN ('dm','connect','view_profile','like')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint("ck_licmd_type", "li_commands", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_licmd_type",
        "li_commands",
        "command_type IN ('dm','connect','view_profile')",
        schema=SCHEMA,
    )
    op.drop_constraint("ck_steps_channel", "sequence_steps", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_steps_channel",
        "sequence_steps",
        "channel IN ('email','linkedin_dm','linkedin_connect','call','sms','whatsapp')",
        schema=SCHEMA,
    )

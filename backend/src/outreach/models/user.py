from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from outreach.db import Base
from outreach.models._mixins import SCHEMA, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Scheduling link (Calendly / Cal.com / Google Calendar appointment page).
    # Rendered into step templates as {{meeting_link}} — see template_render.py.
    meeting_link: Mapped[str | None] = mapped_column(String(500), nullable=True)

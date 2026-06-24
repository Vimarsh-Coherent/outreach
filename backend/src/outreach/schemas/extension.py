from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LinkedInChannelCreate(BaseModel):
    display_label: str = Field(min_length=1, max_length=120)
    daily_cap: int = Field(default=40, ge=1, le=200)


class LinkedInChannelCreated(BaseModel):
    """One-time response — `raw_token` is returned ONCE then discarded server-side.

    User pastes platform_url + channel_id + raw_token into the extension popup.
    Subsequent reads of the channel return only the hash.
    """
    id: int
    display_label: str
    daily_cap: int
    raw_token: str


class CommandOut(BaseModel):
    id: int
    command_type: Literal["dm", "connect", "like_posts"]
    target_li_url: str
    body_text: str


class CommandCompleteRequest(BaseModel):
    status: Literal["done", "failed"]
    provider_message_id: str | None = Field(default=None, max_length=200)
    error: str | None = Field(default=None, max_length=500)


class InboundReplyItem(BaseModel):
    li_url: str | None = Field(default=None, max_length=500)
    thread_id: str | None = Field(default=None, max_length=200)
    message_id: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=8000)
    received_at: datetime
    from_name: str | None = Field(default=None, max_length=200)


class InboundReplyResult(BaseModel):
    matched: int
    inserted: int
    duplicates: int


class ConnectionSeenItem(BaseModel):
    """A LinkedIn profile the extension observed as a 1st-degree connection.

    Posted opportunistically while the user browses My Network / notifications.
    The backend only acts on URLs belonging to an enrolment that is currently
    awaiting acceptance, so reporting extra (non-lead) connections is harmless.
    """
    li_url: str = Field(min_length=1, max_length=500)
    accepted_at: datetime | None = None
    source: str | None = Field(default=None, max_length=40)


class ConnectionSeenResult(BaseModel):
    matched: int   # awaiting enrolments matched by a reported URL
    released: int  # gated DM steps unblocked / scheduled

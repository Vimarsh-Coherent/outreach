from datetime import datetime, time
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

# ---------- Steps (discriminated union per channel) ----------


class _StepBase(BaseModel):
    step_order: int | None = Field(default=None, ge=1, le=50)
    delay_days: int = Field(default=0, ge=0, le=365)
    delay_hours: int = Field(default=0, ge=0, le=23)
    config: dict = Field(default_factory=dict)


class EmailStepCreate(_StepBase):
    channel: Literal["email"]
    subject: str = Field(min_length=1, max_length=250)
    body: str = Field(min_length=1, max_length=16000)


class LinkedInDmStepCreate(_StepBase):
    channel: Literal["linkedin_dm"]
    subject: None = None
    body: str = Field(min_length=1, max_length=8000)


class LinkedInConnectStepCreate(_StepBase):
    channel: Literal["linkedin_connect"]
    subject: None = None
    body: str = Field(min_length=1, max_length=300)


StepCreate = Annotated[
    Union[EmailStepCreate, LinkedInDmStepCreate, LinkedInConnectStepCreate],
    Field(discriminator="channel"),
]


class StepOut(BaseModel):
    id: int
    sequence_id: int
    step_order: int
    channel: str
    delay_days: int
    delay_hours: int
    subject: str | None
    body: str
    config: dict
    created_at: datetime
    updated_at: datetime


# ---------- Sequences ----------

SequenceStatus = Literal["draft", "active", "paused", "archived"]


class SequenceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    timezone: str = Field(default="Asia/Kolkata", max_length=64)
    send_window_start: time = time(9, 0)
    send_window_end: time = time(18, 0)
    send_days_mask: int = Field(default=31, ge=1, le=127)
    ai_followups_enabled: bool = False


class SequenceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    timezone: str | None = Field(default=None, max_length=64)
    send_window_start: time | None = None
    send_window_end: time | None = None
    send_days_mask: int | None = Field(default=None, ge=1, le=127)
    ai_followups_enabled: bool | None = None


class StatusChange(BaseModel):
    status: SequenceStatus


class SequenceOut(BaseModel):
    id: int
    name: str
    description: str | None
    status: SequenceStatus
    timezone: str
    send_window_start: time
    send_window_end: time
    send_days_mask: int
    ai_followups_enabled: bool
    step_count: int
    active_enrolments: int
    created_at: datetime
    updated_at: datetime


class SequenceDetail(SequenceOut):
    steps: list[StepOut]


class SequenceGenerateRequest(BaseModel):
    prompt: str = Field(min_length=10, max_length=4000)
    timezone: str = Field(default="Asia/Kolkata", max_length=64)
    document_ids: list[int] = Field(default_factory=list)


class SequenceGenerateResponse(BaseModel):
    sequence: SequenceDetail
    warnings: list[str] = Field(default_factory=list)


class ReorderRequest(BaseModel):
    step_ids: list[int] = Field(min_length=1, max_length=50)


# ---------- Enrolments ----------

EnrolmentStatus = Literal[
    "active", "paused", "done",
    "stopped_reply", "stopped_bounce", "stopped_manual",
    "stopped_archived", "errored",
]


class EnrolmentCreate(BaseModel):
    lead_ids: list[int] = Field(min_length=1, max_length=5000)


class EnrolmentResult(BaseModel):
    enrolled: int
    deduped: int
    skipped_no_identity: int


class EnrolmentOut(BaseModel):
    id: int
    sequence_id: int
    lead_id: int
    contact_snapshot: dict
    status: EnrolmentStatus
    current_step_order: int
    next_send_at: datetime | None
    enrolled_at: datetime
    stopped_at: datetime | None
    stopped_reason: str | None

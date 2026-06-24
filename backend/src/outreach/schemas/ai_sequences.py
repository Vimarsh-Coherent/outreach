from __future__ import annotations

from datetime import time
from typing import Literal

from pydantic import BaseModel, Field

from outreach.schemas.sequences import SequenceDetail, StepOut


class KnowledgeUploadResult(BaseModel):
    knowledge_id: str
    files_processed: int
    chunks_indexed: int
    filenames: list[str]


class AISequenceGenerateRequest(BaseModel):
    prompt: str = Field(min_length=10, max_length=8000)
    knowledge_id: str | None = Field(default=None, max_length=64)


class AIStepDraft(BaseModel):
    channel: Literal[
        "email", "linkedin_dm", "linkedin_connect", "linkedin_like", "call", "sms", "whatsapp"
    ]
    delay_days: int = Field(default=0, ge=0, le=365)
    delay_hours: int = Field(default=0, ge=0, le=23)
    subject: str | None = None
    body: str
    config: dict = Field(default_factory=dict)


class AISequenceDraft(BaseModel):
    name: str
    description: str | None = None
    timezone: str = "Asia/Kolkata"
    send_window_start: str = "09:00"
    send_window_end: str = "18:00"
    send_days_mask: int = 31
    ai_followups_enabled: bool = True
    steps: list[AIStepDraft]


class AISequenceGenerateResponse(BaseModel):
    draft: AISequenceDraft
    knowledge_snippets_used: int


class AISequenceSaveRequest(BaseModel):
    draft: AISequenceDraft
    knowledge_id: str | None = None


class AIStepRegenerateRequest(BaseModel):
    prompt: str | None = Field(default=None, max_length=4000)


class AIStepRegenerateResponse(BaseModel):
    step: StepOut
    sequence: SequenceDetail

from pydantic import BaseModel, EmailStr, Field


class DraftRequest(BaseModel):
    lead_id: int
    sequence_id: int | None = None
    template_subject: str | None = Field(default=None, max_length=250)
    template_body: str | None = Field(default=None, max_length=16000)


class DraftResponse(BaseModel):
    subject: str
    body: str
    notes: str
    template_subject: str
    template_body: str
    similar_snippets: list[str]
    prior_history_count: int
    model: str


class SendRequest(BaseModel):
    lead_id: int
    to_email: EmailStr
    subject: str = Field(min_length=1, max_length=250)
    body: str = Field(min_length=1, max_length=16000)
    channel_id: int | None = None


class SendResponse(BaseModel):
    ok: bool
    detail: str
    provider_message_id: str | None = None

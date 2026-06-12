from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LeadField = Literal["email", "first_name", "last_name", "phone", "linkedin_url", "company", "title"]


class LeadCreate(BaseModel):
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    company: str | None = None
    title: str | None = None


class LeadOut(BaseModel):
    id: int
    email: str | None
    phone: str | None
    linkedin_url: str | None
    first_name: str | None
    last_name: str | None
    company: str | None
    title: str | None
    source: str | None
    created_at: datetime
    updated_at: datetime


class LeadListResponse(BaseModel):
    items: list[LeadOut]
    total: int
    limit: int
    offset: int


class UploadPreviewResponse(BaseModel):
    token: str
    row_count: int
    columns: list[str]
    sample_rows: list[dict]
    suggested_mapping: dict[LeadField, str | None]


class UploadCommitRequest(BaseModel):
    token: str
    mapping: dict[LeadField, str | None]
    source: str = Field(default="csv", max_length=40)


class UploadCommitResponse(BaseModel):
    inserted: int
    updated: int
    merged_within_upload: int = 0
    skipped_no_identity: int
    skipped_invalid: int

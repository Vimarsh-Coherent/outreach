from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

RagDocumentStatus = Literal["pending", "indexed", "failed"]


class SequenceRagDocumentOut(BaseModel):
    id: int
    filename: str
    mime_type: str
    file_size: int
    status: RagDocumentStatus
    chunk_count: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class SequenceRagUploadResponse(BaseModel):
    documents: list[SequenceRagDocumentOut]

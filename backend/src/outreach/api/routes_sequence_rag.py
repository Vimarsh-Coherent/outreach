from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.deps import get_current_user
from outreach.models.user import User
from outreach.schemas.sequence_rag import SequenceRagDocumentOut, SequenceRagUploadResponse
from outreach.services import document_ingest

router = APIRouter(prefix="/api/sequences/rag", tags=["sequence-rag"])


@router.post("/documents", response_model=SequenceRagUploadResponse, status_code=201)
async def upload_documents(
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SequenceRagUploadResponse:
    if not files:
        raise HTTPException(400, "at least one file required")
    docs: list[SequenceRagDocumentOut] = []
    for f in files:
        docs.append(await document_ingest.ingest_document(session, user.id, f))
    return SequenceRagUploadResponse(documents=docs)


@router.get("/documents", response_model=list[SequenceRagDocumentOut])
async def list_documents(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SequenceRagDocumentOut]:
    return await document_ingest.list_documents(session, user.id)


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(
    doc_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    if not await document_ingest.delete_document(session, user.id, doc_id):
        raise HTTPException(404, "document not found")

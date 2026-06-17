from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.deps import get_current_user
from outreach.models.user import User
from outreach.schemas.ai_sequences import (
    AISequenceGenerateRequest,
    AISequenceGenerateResponse,
    AISequenceSaveRequest,
    AIStepRegenerateRequest,
    AIStepRegenerateResponse,
    KnowledgeUploadResult,
)
from outreach.schemas.sequences import SequenceDetail
from outreach.services import ai_sequence_generator
from outreach.services.knowledge_ingest import ALLOWED_EXTENSIONS

router = APIRouter(prefix="/api/ai-sequences", tags=["ai-sequences"])

MAX_FILES = 10
MAX_FILE_BYTES = 10 * 1024 * 1024


@router.post("/knowledge/upload", response_model=KnowledgeUploadResult)
async def upload_knowledge(
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
) -> KnowledgeUploadResult:
    if not files:
        raise HTTPException(400, "at least one file required")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"maximum {MAX_FILES} files per upload")

    payloads: list[tuple[str, bytes]] = []
    for f in files:
        ext = (f.filename or "").rsplit(".", 1)[-1].lower()
        if f".{ext}" not in ALLOWED_EXTENSIONS:
            raise HTTPException(400, f"unsupported file: {f.filename}. Use PDF, DOCX, TXT, or CSV")
        content = await f.read()
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(400, f"file too large: {f.filename} (max 10MB)")
        payloads.append((f.filename or "upload.txt", content))

    knowledge_id, indexed, filenames = await ai_sequence_generator.upload_knowledge(
        user.id, payloads
    )
    return KnowledgeUploadResult(
        knowledge_id=knowledge_id,
        files_processed=len(filenames),
        chunks_indexed=indexed,
        filenames=filenames,
    )


@router.post("/generate", response_model=AISequenceGenerateResponse)
async def generate_sequence(
    dto: AISequenceGenerateRequest,
    user: User = Depends(get_current_user),
) -> AISequenceGenerateResponse:
    draft, snippet_count = await ai_sequence_generator.generate_draft(user.id, dto)
    return AISequenceGenerateResponse(draft=draft, knowledge_snippets_used=snippet_count)


@router.post("/save", response_model=SequenceDetail, status_code=201)
async def save_sequence(
    dto: AISequenceSaveRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SequenceDetail:
    if not dto.draft.steps:
        raise HTTPException(400, "draft has no steps")
    return await ai_sequence_generator.save_draft(
        session, user.id, dto.draft, dto.knowledge_id
    )


@router.post(
    "/sequences/{sequence_id}/steps/{step_id}/regenerate",
    response_model=AIStepRegenerateResponse,
)
async def regenerate_step(
    sequence_id: int,
    step_id: int,
    dto: AIStepRegenerateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AIStepRegenerateResponse:
    detail = await ai_sequence_generator.regenerate_step(
        session, user.id, sequence_id, step_id, dto.prompt
    )
    step = next(s for s in detail.steps if s.id == step_id)
    return AIStepRegenerateResponse(step=step, sequence=detail)

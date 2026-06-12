from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.deps import get_current_user
from outreach.models.user import User
from outreach.schemas.leads import (
    LeadCreate,
    LeadListResponse,
    LeadOut,
    UploadCommitRequest,
    UploadCommitResponse,
    UploadPreviewResponse,
)
from outreach.services import leads_service
from outreach.services.identity import canonical_identity, linkedin_url_from_slug
from outreach.services.lead_upload import (
    auto_map,
    iter_mapped_rows,
    preview_dataframe,
    resolve_stashed,
    stash_upload,
)

router = APIRouter(prefix="/api/leads", tags=["leads"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@router.post("/upload/preview", response_model=UploadPreviewResponse)
async def upload_preview(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> UploadPreviewResponse:
    if not file.filename:
        raise HTTPException(400, "missing filename")
    suffix = file.filename.lower().rsplit(".", 1)[-1]
    if suffix not in ("csv", "tsv", "xlsx", "xls"):
        raise HTTPException(400, "supported formats: csv, tsv, xlsx, xls")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
    if not content:
        raise HTTPException(400, "empty file")
    token, path = stash_upload(user.id, file.filename, content)
    try:
        columns, sample, total = preview_dataframe(path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"failed to parse file: {type(e).__name__}: {e}") from e
    mapping = auto_map(columns)
    return UploadPreviewResponse(
        token=token,
        row_count=total,
        columns=columns,
        sample_rows=sample,
        suggested_mapping=mapping,  # type: ignore[arg-type]
    )


@router.post("/upload/commit", response_model=UploadCommitResponse)
async def upload_commit(
    req: UploadCommitRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UploadCommitResponse:
    try:
        path = resolve_stashed(user.id, req.token)
    except FileNotFoundError as e:
        raise HTTPException(404, "upload token not found or expired") from e
    rows = iter_mapped_rows(path, dict(req.mapping))
    stats = await leads_service.upsert_leads(session, user.id, rows, source=req.source)
    return UploadCommitResponse(**stats.to_dict())


@router.get("", response_model=LeadListResponse)
async def list_leads(
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LeadListResponse:
    items, total = await leads_service.list_leads(
        session, user.id, search=search, limit=limit, offset=offset
    )
    return LeadListResponse(
        items=[LeadOut.model_validate(i, from_attributes=True) for i in items],
        total=total, limit=limit, offset=offset,
    )


@router.post("", response_model=LeadOut, status_code=201)
async def create_lead(
    dto: LeadCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LeadOut:
    ident = canonical_identity(email=dto.email, phone=dto.phone, linkedin=dto.linkedin_url)
    if ident.hash is None:
        raise HTTPException(400, "at least one of email, phone, or linkedin_url is required")
    payload = [{
        "email": ident.email,
        "phone": ident.phone,
        "linkedin_url": linkedin_url_from_slug(ident.linkedin_slug),
        "first_name": dto.first_name,
        "last_name": dto.last_name,
        "company": dto.company,
        "title": dto.title,
    }]
    await leads_service.upsert_leads(session, user.id, payload, source="manual")
    # Return the freshly upserted row
    items, _ = await leads_service.list_leads(session, user.id, search=ident.email or ident.phone, limit=1, offset=0)
    if not items:
        raise HTTPException(500, "upsert succeeded but lookup failed")
    return LeadOut.model_validate(items[0], from_attributes=True)


@router.delete("/{lead_id}", status_code=204)
async def delete_lead(
    lead_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    if not await leads_service.delete_lead(session, user.id, lead_id):
        raise HTTPException(404, "lead not found")


@router.delete("", status_code=200)
async def delete_all_leads(
    confirm: bool = Query(default=False),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not confirm:
        raise HTTPException(400, "pass ?confirm=true to wipe all leads")
    deleted = await leads_service.delete_all_leads(session, user.id)
    return {"deleted": deleted}

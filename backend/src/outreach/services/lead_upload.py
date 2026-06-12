import io
from pathlib import Path
from uuid import uuid4

import pandas as pd

from outreach.config import get_settings

LEAD_FIELDS = (
    "email", "first_name", "last_name", "phone",
    "linkedin_url", "company", "title",
)

COLUMN_HINTS: dict[str, list[str]] = {
    "email": ["email", "email address", "e-mail", "e mail", "emailaddress", "mail"],
    "first_name": ["first name", "first", "firstname", "given name", "given_name", "fname"],
    "last_name": ["last name", "last", "lastname", "surname", "family name", "family_name", "lname"],
    "phone": ["phone", "phone number", "phonenumber", "mobile", "cell", "tel", "telephone", "phone_number"],
    "linkedin_url": ["linkedin", "linkedin url", "linkedin_url", "linkedin profile", "li", "li_url", "linkedinprofile"],
    "company": ["company", "company name", "company_name", "organization", "organisation", "org", "employer", "account"],
    "title": ["title", "job title", "job_title", "position", "role", "designation"],
}


def _normkey(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum() or ch == " ").strip()


def auto_map(columns: list[str]) -> dict[str, str | None]:
    """Map target lead fields -> source column header (or None)."""
    norm_to_orig = {_normkey(c): c for c in columns}
    out: dict[str, str | None] = {}
    used: set[str] = set()
    for target, hints in COLUMN_HINTS.items():
        match: str | None = None
        for hint in hints:
            nk = _normkey(hint)
            if nk in norm_to_orig and norm_to_orig[nk] not in used:
                match = norm_to_orig[nk]
                break
        if match:
            used.add(match)
        out[target] = match
    return out


def _read_dataframe(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path, dtype=str)
    # CSV/TSV with delimiter sniffing + utf-8 fallback to latin-1
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return pd.read_csv(path, dtype=str, sep=None, engine="python", encoding=enc, on_bad_lines="skip")
        except UnicodeDecodeError:
            continue
    raise ValueError("could not decode file with utf-8 or latin-1")


def stash_upload(user_id: int, filename: str, content: bytes) -> tuple[str, Path]:
    settings = get_settings()
    base = Path(settings.vault_dir).parent / "uploads" / str(user_id)
    base.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix.lower() or ".csv"
    token = uuid4().hex
    out_path = base / f"{token}{ext}"
    out_path.write_bytes(content)
    return token, out_path


def resolve_stashed(user_id: int, token: str) -> Path:
    settings = get_settings()
    base = Path(settings.vault_dir).parent / "uploads" / str(user_id)
    for p in base.glob(f"{token}.*"):
        if p.is_file():
            return p
    raise FileNotFoundError(f"upload token {token} not found")


def preview_dataframe(path: Path, sample_rows: int = 10) -> tuple[list[str], list[dict], int]:
    df = _read_dataframe(path)
    df = df.fillna("")
    columns = [str(c) for c in df.columns]
    sample = df.head(sample_rows).to_dict(orient="records")
    return columns, sample, len(df)


def iter_mapped_rows(path: Path, mapping: dict[str, str | None]):
    df = _read_dataframe(path).fillna("")
    columns = [str(c) for c in df.columns]
    # Build (target -> column_index) for fast access
    col_index: dict[str, int] = {}
    for target, source in mapping.items():
        if source and source in columns:
            col_index[target] = columns.index(source)
    for row in df.itertuples(index=False, name=None):
        mapped: dict[str, str] = {}
        for target, idx in col_index.items():
            val = row[idx]
            if val is None:
                continue
            s = str(val).strip()
            if s:
                mapped[target] = s
        if mapped:
            yield mapped

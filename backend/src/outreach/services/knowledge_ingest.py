"""Extract text from knowledge-base uploads and chunk for embedding."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pandas as pd

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".csv", ".tsv"}

CHUNK_SIZE = 900
CHUNK_OVERLAP = 120


def _chunk_text(text: str, *, source: str) -> list[dict[str, str]]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    chunks: list[dict[str, str]] = []
    start = 0
    idx = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + CHUNK_SIZE)
        piece = cleaned[start:end].strip()
        if piece:
            chunks.append({"text": piece, "source": source, "chunk_index": str(idx)})
            idx += 1
        if end >= len(cleaned):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader  # type: ignore[import-not-found]

    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _extract_docx(path: Path) -> str:
    from docx import Document  # type: ignore[import-not-found]

    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _extract_csv(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            df = pd.read_csv(path, dtype=str, sep=None, engine="python", encoding=enc, on_bad_lines="skip")
            break
        except UnicodeDecodeError:
            continue
    else:
        df = pd.read_csv(path, dtype=str, on_bad_lines="skip")
    rows: list[str] = []
    for _, row in df.head(500).iterrows():
        cells = [f"{c}: {v}" for c, v in row.items() if str(v).strip() and str(v) != "nan"]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def extract_file_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS and ext not in (".tsv",):
        raise ValueError(f"unsupported file type: {ext}")
    if ext == ".pdf":
        return _extract_pdf(path)
    if ext == ".docx":
        return _extract_docx(path)
    if ext in (".csv", ".tsv"):
        return _extract_csv(path)
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="latin-1", errors="ignore")


def extract_and_chunk(filename: str, content: bytes) -> list[dict[str, str]]:
    ext = Path(filename).suffix.lower() or ".txt"
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"unsupported file type: {ext}. Allowed: PDF, DOCX, TXT, CSV")
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        text = extract_file_text(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return _chunk_text(text, source=filename)

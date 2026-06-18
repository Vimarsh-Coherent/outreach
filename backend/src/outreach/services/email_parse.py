"""Inbound message parsing — classifies a raw rfc822 byte string into one of:

  - reply    : real human reply, threaded off one of our HMAC Message-IDs
  - bounce   : DSN (delivery-status report)
  - auto_reply: out-of-office / vacation / mailer-daemon-style auto-response
  - unrelated: not for us — drop

The classifier returns a `ParsedInbound` record with the extracted bits the
reply processor needs (matched_step_run_id, original_message_id, body snippet, etc.).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import Message
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

from outreach.services.threading_email import parse_message_id

# OOO heuristics — verbatim from PLAN.md §5.7.
OOO_SUBJECT_RX = [
    re.compile(r"^out of office", re.I),
    re.compile(r"^auto[- ]?reply", re.I),
    re.compile(r"^automatic reply", re.I),
    re.compile(r"^away from (the )?office", re.I),
    re.compile(r"^on (vacation|holiday|leave)", re.I),
]
NOREPLY_FROM_RX = re.compile(
    r"^(no[-_]?reply|postmaster|mailer[-_]?daemon|donotreply)@", re.I
)

InboundKind = str  # 'reply' | 'bounce' | 'auto_reply' | 'unrelated'


@dataclass(slots=True)
class ParsedInbound:
    kind: InboundKind
    matched_step_run_id: int | None = None
    inbound_message_id: str | None = None
    subject: str | None = None
    from_addr: str | None = None
    body_snippet: str | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    bounce_detail: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    # Which channel this inbound arrived on — tags the resulting Event. Defaults
    # to email (the IMAP poller path); the WhatsApp webhook sets "whatsapp".
    channel: str = "email"


def _flatten_headers(msg: Message) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in msg.items():
        key = k.lower()
        # If a header appears multiple times keep the last (rare for the ones we care about).
        out[key] = str(v)
    return out


def _is_auto_reply(headers: dict[str, str], from_addr: str, subject: str) -> bool:
    auto_submitted = (headers.get("auto-submitted") or "").lower()
    if auto_submitted and auto_submitted != "no":
        return True
    if "x-autoreply" in headers or "x-autorespond" in headers:
        return True
    precedence = (headers.get("precedence") or "").lower()
    if precedence in ("bulk", "auto_reply", "auto-reply"):
        return True
    if from_addr and NOREPLY_FROM_RX.search(from_addr.lower()):
        return True
    if subject and any(rx.search(subject) for rx in OOO_SUBJECT_RX):
        return True
    return False


def _is_dsn(msg: Message) -> bool:
    """Detect a delivery-status report."""
    ctype = (msg.get_content_type() or "").lower()
    if ctype != "multipart/report":
        return False
    params = msg.get_params(failobj=[])
    for name, value in params:
        if name.lower() == "report-type" and value.lower() in ("delivery-status", "disposition-notification"):
            return True
    # Some servers omit the parameter but still send a delivery-status sub-part.
    for part in msg.walk():
        if (part.get_content_type() or "").lower() == "message/delivery-status":
            return True
    return False


def _extract_thread_ids(headers: dict[str, str]) -> list[str]:
    """Pull all Message-ID candidates out of In-Reply-To + References."""
    ids: list[str] = []
    for key in ("in-reply-to", "references"):
        raw = headers.get(key) or ""
        for m in re.finditer(r"<[^>\s]+>", raw):
            ids.append(m.group(0))
    return ids


def _extract_dsn_thread_ids(msg: Message) -> tuple[list[str], str | None]:
    """For a DSN: dig into the message/rfc822 sub-part (the bounced original)
    and extract the In-Reply-To/References + a Final-Recipient string.

    Returns (thread_id_candidates, final_recipient_or_None).
    """
    thread_ids: list[str] = []
    final_recipient: str | None = None
    for part in msg.walk():
        ctype = (part.get_content_type() or "").lower()
        if ctype == "message/rfc822":
            payload = part.get_payload()
            if isinstance(payload, list) and payload:
                inner = payload[0]
                if isinstance(inner, Message):
                    in_id = inner.get("Message-ID")
                    if in_id:
                        thread_ids.append(str(in_id))
                    # Also pull from In-Reply-To / References on the bounced original.
                    for h in ("In-Reply-To", "References"):
                        v = inner.get(h)
                        if v:
                            for m in re.finditer(r"<[^>\s]+>", str(v)):
                                thread_ids.append(m.group(0))
        elif ctype == "message/delivery-status":
            # Each sub-part has its own block of fields. Pull Final-Recipient + Original-Message-ID.
            payload = part.get_payload()
            blocks = payload if isinstance(payload, list) else [part]
            for sub in blocks:
                if isinstance(sub, Message):
                    fr = sub.get("Final-Recipient") or sub.get("Original-Recipient")
                    if fr and final_recipient is None:
                        # "rfc822; me@example.com" -> "me@example.com"
                        m = re.search(r"[\w.+-]+@[\w.-]+", str(fr))
                        if m:
                            final_recipient = m.group(0)
                    om = sub.get("Original-Message-ID")
                    if om:
                        for m in re.finditer(r"<[^>\s]+>", str(om)):
                            thread_ids.append(m.group(0))
    return thread_ids, final_recipient


def _first_text_body(msg: Message, max_len: int = 4000) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if (part.get_content_type() or "").lower() == "text/plain":
                try:
                    return str(part.get_payload(decode=True) or b"", errors="replace")[:max_len]
                except Exception:  # noqa: BLE001
                    continue
        # fall back to first text/html stripped of tags
        for part in msg.walk():
            if (part.get_content_type() or "").lower() == "text/html":
                try:
                    raw = str(part.get_payload(decode=True) or b"", errors="replace")
                    return re.sub(r"<[^>]+>", " ", raw)[:max_len]
                except Exception:  # noqa: BLE001
                    continue
        return ""
    payload = msg.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")[:max_len]
    return str(msg.get_payload() or "")[:max_len]


def _from_addr(headers: dict[str, str]) -> str:
    raw = headers.get("from") or ""
    # "Name <addr@host>" -> "addr@host"
    m = re.search(r"<([^>]+)>", raw)
    if m:
        return m.group(1).strip()
    return raw.strip()


def parse_inbound(raw_bytes: bytes) -> ParsedInbound:
    msg = message_from_bytes(raw_bytes)
    headers = _flatten_headers(msg)
    subject = headers.get("subject")
    from_addr = _from_addr(headers)
    inbound_mid = headers.get("message-id")
    received_at = datetime.now(timezone.utc)
    if "date" in headers:
        try:
            received_at = parsedate_to_datetime(headers["date"]).astimezone(timezone.utc)
        except Exception:  # noqa: BLE001
            pass
    body_snippet = _first_text_body(msg, max_len=500)

    # 1. DSN takes priority.
    if _is_dsn(msg):
        candidates, final_recipient = _extract_dsn_thread_ids(msg)
        run_id: int | None = None
        for cand in candidates:
            r = parse_message_id(cand)
            if r is not None:
                run_id = r
                break
        return ParsedInbound(
            kind="bounce" if run_id is not None else "unrelated",
            matched_step_run_id=run_id,
            inbound_message_id=inbound_mid,
            subject=subject, from_addr=from_addr,
            body_snippet=body_snippet, received_at=received_at,
            bounce_detail=final_recipient,
            headers=headers,
        )

    # 2. Pull threading IDs from the inbound headers — any HMAC-verifying one wins.
    candidates = _extract_thread_ids(headers)
    matched: int | None = None
    for cand in candidates:
        r = parse_message_id(cand)
        if r is not None:
            matched = r
            break

    # 3. If no thread match, this email isn't ours.
    if matched is None:
        return ParsedInbound(
            kind="unrelated", inbound_message_id=inbound_mid,
            subject=subject, from_addr=from_addr,
            body_snippet=body_snippet, received_at=received_at, headers=headers,
        )

    # 4. We matched a step_run — now decide reply vs auto_reply.
    if _is_auto_reply(headers, from_addr, subject or ""):
        kind: InboundKind = "auto_reply"
    else:
        kind = "reply"

    return ParsedInbound(
        kind=kind, matched_step_run_id=matched, inbound_message_id=inbound_mid,
        subject=subject, from_addr=from_addr,
        body_snippet=body_snippet, received_at=received_at, headers=headers,
    )

"""A/B testing for sequence steps.

Variants live in ``sequence_steps.config['variants']`` as a list of
``{label, subject?, body, weight?}``. At send time a variant is chosen
*deterministically per enrolment* (so a lead always gets the same variant, but
the split across leads honors the weights), and ``step_runs.variant_label``
records it. Opens/clicks/replies are then attributed per variant for winner
stats.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(slots=True)
class ResolvedMessage:
    subject: str | None
    body: str
    variant_label: str | None


def normalize_variants(raw: object) -> list[dict]:
    """Validate + clean a step's config['variants']. Returns [] when there are
    fewer than 2 usable variants (i.e. A/B not actually configured)."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for i, v in enumerate(raw):
        if not isinstance(v, dict):
            continue
        body = v.get("body")
        if not isinstance(body, str) or not body.strip():
            continue  # a variant must have a body
        label = str(v.get("label") or chr(ord("A") + i))[:40]
        try:
            weight = int(v.get("weight", 1))
        except (TypeError, ValueError):
            weight = 1
        weight = max(1, min(1000, weight))
        subject = v.get("subject")
        out.append({
            "label": label,
            "subject": subject if isinstance(subject, str) else None,
            "body": body,
            "weight": weight,
        })
    return out if len(out) >= 2 else []


def pick_variant(variants: list[dict], seed: str) -> dict:
    """Deterministic weighted choice. Same seed → same variant (stable per
    enrolment); distribution across seeds honors the weights."""
    weights = [int(v.get("weight", 1)) for v in variants]
    total = sum(weights)
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % total
    acc = 0
    for v, w in zip(variants, weights):
        acc += w
        if h < acc:
            return v
    return variants[-1]


def resolve_message(
    *, step_config: dict, default_subject: str | None, default_body: str, seed: str,
) -> ResolvedMessage:
    """Pick the variant (if any) for this enrolment and return the message to
    send + the variant_label to stamp (None when the step has no A/B test)."""
    variants = normalize_variants((step_config or {}).get("variants"))
    if not variants:
        return ResolvedMessage(subject=default_subject, body=default_body, variant_label=None)
    v = pick_variant(variants, seed)
    return ResolvedMessage(
        subject=v["subject"] if v["subject"] is not None else default_subject,
        body=v["body"],
        variant_label=v["label"],
    )


async def variant_stats(session: AsyncSession, step_id: int) -> list[dict]:
    """Per-variant performance for a step: sent / opened / clicked / replied /
    positive, with rates. Attributed via step_runs.variant_label joined to events."""
    rows = (await session.execute(text("""
        SELECT sr.variant_label AS label,
               count(*) FILTER (WHERE sr.status = 'sent')                        AS sent,
               count(DISTINCT ev.step_run_id) FILTER (WHERE ev.event_type='open')  AS opened,
               count(DISTINCT ev.step_run_id) FILTER (WHERE ev.event_type='click') AS clicked,
               count(DISTINCT ev.step_run_id) FILTER (WHERE ev.event_type='reply') AS replied,
               count(DISTINCT ev.step_run_id) FILTER (
                   WHERE ev.event_type='reply'
                     AND rs.label IN ('positive','interested')
               ) AS positive
          FROM outreach.step_runs sr
          LEFT JOIN outreach.events ev ON ev.step_run_id = sr.id
          LEFT JOIN outreach.reply_sentiment rs ON rs.event_id = ev.id
         WHERE sr.step_id = :sid AND sr.variant_label IS NOT NULL
         GROUP BY sr.variant_label
         ORDER BY sr.variant_label
    """), {"sid": step_id})).mappings().all()

    out: list[dict] = []
    for r in rows:
        sent = int(r["sent"] or 0)
        def rate(n: int) -> float:
            return round((n / sent) * 100, 1) if sent else 0.0
        out.append({
            "label": r["label"],
            "sent": sent,
            "opened": int(r["opened"] or 0),
            "clicked": int(r["clicked"] or 0),
            "replied": int(r["replied"] or 0),
            "positive": int(r["positive"] or 0),
            "open_rate": rate(int(r["opened"] or 0)),
            "click_rate": rate(int(r["clicked"] or 0)),
            "reply_rate": rate(int(r["replied"] or 0)),
            "positive_rate": rate(int(r["positive"] or 0)),
        })
    return out


def pick_winner(stats: list[dict], *, min_sample: int = 20) -> str | None:
    """Suggest a winner by reply rate (tie-break: positive rate), once every
    variant has at least `min_sample` sends. None = not enough data yet."""
    if len(stats) < 2 or any(s["sent"] < min_sample for s in stats):
        return None
    best = max(stats, key=lambda s: (s["reply_rate"], s["positive_rate"], s["open_rate"]))
    return best["label"]

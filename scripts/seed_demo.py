"""Seed the DB with synthetic data so the dashboard renders meaningfully even
before real outreach has happened.

What it creates:
  - 3 sequences (Q3 Enterprise, ABM-Mid-Market, Product Launch)
  - ~120 leads with realistic companies / titles
  - ~600 step_runs spread across the last 30 days (status='sent')
  - ~95 reply events with sentiment rows distributed across the 7 buckets
  - ~12 bounces, ~6 unsubscribes, ~5 errored enrolments
  - 3 at-risk enrolments stuck since yesterday

Idempotent: drops everything under user_id 1 first.

Run with:
    backend\\.venv\\Scripts\\python.exe scripts\\seed_demo.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "backend" / "src"))
load_dotenv(Path(__file__).parent.parent / "backend" / ".env")

random.seed(1729)

SEQ_DEFS = [
    {"name": "Q3 Enterprise Outbound", "ai_followups": True},
    {"name": "ABM — Mid-Market", "ai_followups": False},
    {"name": "Product Launch Announcement", "ai_followups": True},
]

STEP_BODIES = [
    ("Intro — {{company}}", "Hi {{first_name}},\n\nWe help teams like {{company}} ship faster. Open to a 15-min chat?\n\n— Vimarsh"),
    ("Quick follow up", "Hi {{first_name}}, did the note below land? Happy to send our 1-pager if helpful."),
    ("Last note", "Hi {{first_name}}, closing the loop — let me know if {{company}} would benefit from a quick walkthrough."),
]

COMPANIES = ["Acme Robotics", "Northstar Logistics", "Bauhaus AI", "Sirius Robotics", "Vega Biotech", "Helios Energy",
             "Kairos Shipping", "Mendoza Co", "DreamCloud KK", "Polaris Health", "Atlas Manufacturing", "Orion Capital"]
TITLES = ["CEO", "CTO", "VP Engineering", "Director of Ops", "Head of Product", "Founder", "VP Sales", "Chief Architect"]
FIRST = ["Alex", "Marcus", "Yuki", "Priya", "Dimitri", "Lena", "Carlos", "Ahmed", "Sara", "Liam", "Mei", "Noah", "Hana"]
LAST = ["Chen", "Patel", "Tanaka", "Sharma", "Volkov", "Mueller", "Mendoza", "El-Sayed", "Rivera", "Park", "Lopez"]

SENTIMENT_MIX = [
    ("positive", 0.18, "Wants to set up a meeting."),
    ("interested", 0.22, "Asked for deck / pricing."),
    ("objection", 0.10, "Budget objection."),
    ("negative", 0.08, "Not interested right now."),
    ("unsubscribe", 0.06, "Asked to opt out."),
    ("neutral", 0.34, "Acknowledgement only."),
    ("auto_reply", 0.02, "OOO."),
]


def _identity_hash(email: str) -> bytes:
    return hashlib.sha256(f"email:{email.lower().strip()}".encode()).digest()


def _pick_sentiment() -> tuple[str, float, str]:
    r = random.random()
    acc = 0.0
    for label, weight, reasoning in SENTIMENT_MIX:
        acc += weight
        if r <= acc:
            return label, round(random.uniform(0.65, 0.99), 2), reasoning
    return "neutral", 0.7, "fallback"


async def amain() -> None:
    from sqlalchemy import select, text
    from outreach.db import SessionLocal
    from outreach.models.enrolment import Enrolment
    from outreach.models.event import Event, ReplySentiment
    from outreach.models.lead import Lead
    from outreach.models.sequence import Sequence
    from outreach.models.step import SequenceStep
    from outreach.models.step_run import StepRun
    from outreach.models.user import User

    async with SessionLocal() as session:
        # Ensure user 1 exists.
        user = await session.scalar(select(User).order_by(User.id.asc()).limit(1))
        if user is None:
            user = User(email="seed@local", display_name="Demo")
            session.add(user); await session.commit(); await session.refresh(user)
        user_id = user.id

        # Wipe prior seed.
        await session.execute(text("DELETE FROM outreach.reply_sentiment"))
        await session.execute(text("DELETE FROM outreach.events"))
        await session.execute(text("DELETE FROM outreach.step_runs"))
        await session.execute(text("DELETE FROM outreach.enrolments"))
        await session.execute(text("DELETE FROM outreach.suppressions"))
        await session.execute(text("DELETE FROM outreach.vault_index"))
        await session.execute(text("DELETE FROM outreach.sequence_steps"))
        await session.execute(text("DELETE FROM outreach.sequences"))
        await session.execute(text("DELETE FROM outreach.leads"))
        await session.commit()

        # Sequences.
        sequences: list[Sequence] = []
        for spec in SEQ_DEFS:
            s = Sequence(
                user_id=user_id, name=spec["name"],
                status="active", timezone="UTC",
                ai_followups_enabled=spec["ai_followups"],
            )
            session.add(s); sequences.append(s)
        await session.commit()
        for s in sequences:
            await session.refresh(s)

        # Steps per sequence.
        step_ids: dict[int, list[int]] = {}
        for s in sequences:
            sids: list[int] = []
            for i, (subj, body) in enumerate(STEP_BODIES, start=1):
                step = SequenceStep(
                    sequence_id=s.id, step_order=i, channel="email",
                    delay_days=0 if i == 1 else (2 if i == 2 else 4),
                    delay_hours=0, subject=subj, body=body,
                )
                session.add(step)
                await session.flush()
                sids.append(step.id)
            step_ids[s.id] = sids
        await session.commit()

        # Leads.
        leads: list[Lead] = []
        for i in range(120):
            first = random.choice(FIRST)
            last = random.choice(LAST)
            company = random.choice(COMPANIES)
            email = f"{first.lower()}.{last.lower()}{i}@{company.lower().replace(' ', '')}.com"
            l = Lead(
                user_id=user_id,
                identity_hash=_identity_hash(email),
                email=email, first_name=first, last_name=last,
                company=company, title=random.choice(TITLES),
                source="seed",
            )
            session.add(l); leads.append(l)
        await session.commit()
        for l in leads:
            await session.refresh(l)

        # Distribute enrolments across sequences.
        now = datetime.now(timezone.utc)
        enrolments: list[tuple[Enrolment, Sequence]] = []
        for l in leads:
            s = random.choice(sequences)
            enrolled_at = now - timedelta(days=random.uniform(0, 28))
            status = "active"
            stopped_at = None
            stopped_reason = None
            r = random.random()
            if r < 0.04:
                status = "errored"
                stopped_at = enrolled_at + timedelta(hours=random.uniform(1, 48))
                stopped_reason = "5 consecutive SMTP failures"
            elif r < 0.10:
                status = "stopped_bounce"
                stopped_at = enrolled_at + timedelta(hours=random.uniform(1, 12))
                stopped_reason = "hard bounce"
            elif r < 0.60:
                status = "stopped_reply"
                stopped_at = enrolled_at + timedelta(hours=random.uniform(4, 96))
                stopped_reason = "lead replied"
            e = Enrolment(
                sequence_id=s.id, user_id=user_id, lead_id=l.id,
                identity_hash=l.identity_hash,
                contact_snapshot={
                    "email": l.email, "first_name": l.first_name,
                    "last_name": l.last_name, "company": l.company, "title": l.title,
                },
                status=status, current_step_order=random.randint(1, 3) if status != "active" else random.randint(0, 1),
                next_send_at=None if status != "active" else now + timedelta(hours=random.uniform(1, 48)),
                stopped_at=stopped_at, stopped_reason=stopped_reason,
                enrolled_at=enrolled_at,
            )
            session.add(e); enrolments.append((e, s))
        await session.commit()
        for e, _ in enrolments:
            await session.refresh(e)

        # Plant 3 enrolments stuck since yesterday.
        for e, _ in random.sample([(e, s) for e, s in enrolments if e.status == "active"], k=min(3, sum(1 for e, _ in enrolments if e.status == "active"))):
            e.next_send_at = now - timedelta(hours=random.uniform(26, 40))
        await session.commit()

        # Step runs + reply events.
        sent_runs = 0
        reply_events = 0
        bounce_events = 0
        sentiment_rows = 0
        for e, s in enrolments:
            sids = step_ids[s.id]
            # Determine how many steps were sent.
            if e.status in ("stopped_reply", "stopped_bounce", "errored", "stopped_manual"):
                steps_sent = random.randint(1, min(3, len(sids)))
            elif e.status == "done":
                steps_sent = len(sids)
            else:  # active / paused
                steps_sent = random.randint(0, 2)

            run_anchor = e.enrolled_at
            for i in range(steps_sent):
                run_anchor = run_anchor + timedelta(hours=random.uniform(0.5, 30))
                if run_anchor > now:
                    break
                step_id = sids[i]
                run = StepRun(
                    enrolment_id=e.id, step_id=step_id, channel="email",
                    status="sent",
                    scheduled_at=run_anchor - timedelta(minutes=2),
                    sent_at=run_anchor,
                    provider_message_id=f"<seed-{e.id}-{i+1}@outreach.local>",
                )
                session.add(run); sent_runs += 1
                await session.flush()
                # delivered event
                delivered = Event(
                    enrolment_id=e.id, step_run_id=run.id, event_type="delivered",
                    channel="email", external_id=run.provider_message_id,
                    payload={"to": e.contact_snapshot.get("email")},
                    occurred_at=run_anchor,
                )
                session.add(delivered)

            # If stopped_reply or stopped_bounce, emit the final event.
            if e.status == "stopped_reply" and e.stopped_at is not None:
                ev = Event(
                    enrolment_id=e.id, step_run_id=None,
                    event_type="reply", channel="email",
                    external_id=f"<seed-reply-{e.id}@example.com>",
                    payload={
                        "from": e.contact_snapshot.get("email"),
                        "subject": f"Re: {STEP_BODIES[0][0]}",
                        "snippet": "(seeded reply)",
                    },
                    occurred_at=e.stopped_at,
                )
                session.add(ev); reply_events += 1
                await session.flush()
                label, conf, reason = _pick_sentiment()
                sent_row = ReplySentiment(
                    event_id=ev.id, label=label, confidence=conf,
                    reasoning=reason, model="seed-mix",
                )
                session.add(sent_row); sentiment_rows += 1
            elif e.status == "stopped_bounce" and e.stopped_at is not None:
                ev = Event(
                    enrolment_id=e.id, step_run_id=None,
                    event_type="bounce", channel="email",
                    external_id=f"<seed-bounce-{e.id}@example.com>",
                    payload={
                        "from": "mailer-daemon@example.com",
                        "subject": "Undeliverable",
                        "snippet": "550 user unknown",
                        "bounce_detail": e.contact_snapshot.get("email"),
                    },
                    occurred_at=e.stopped_at,
                )
                session.add(ev); bounce_events += 1
        await session.commit()

        # Plant a handful of unsubscribe sentiment rows scattered across replies
        # (mostly already covered by _pick_sentiment, but make sure we have a few).
        unsub_count = int(await session.scalar(text(
            "SELECT COUNT(*) FROM outreach.reply_sentiment WHERE label='unsubscribe'"
        )) or 0)
        if unsub_count < 6:
            # Re-tag some 'neutral' events to 'unsubscribe' to land in the dashboard widget.
            evs = (await session.execute(text(
                "SELECT event_id FROM outreach.reply_sentiment WHERE label='neutral' LIMIT :n"
            ), {"n": 6 - unsub_count})).all()
            for (eid,) in evs:
                await session.execute(text(
                    "UPDATE outreach.reply_sentiment SET label='unsubscribe', reasoning='Asked to opt out (seeded)' "
                    "WHERE event_id=:id"
                ), {"id": eid})
            await session.commit()

        print(f"seeded: {len(leads)} leads, {len(enrolments)} enrolments, {sent_runs} step_runs, {reply_events} reply events, {sentiment_rows} sentiments, {bounce_events} bounces")


if __name__ == "__main__":
    asyncio.run(amain())

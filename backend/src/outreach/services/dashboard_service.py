from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.schemas.dashboard import (
    AtRiskEnrolment,
    DashboardSummary,
    HotLead,
    SentimentBucket,
    SentimentTimeseriesResponse,
    SequenceStats,
)


async def summary(session: AsyncSession, user_id: int, days: int = 30) -> DashboardSummary:
    since = datetime.now(timezone.utc) - timedelta(days=days)

    enrolled = int(await session.scalar(text(
        "SELECT COUNT(*) FROM outreach.enrolments "
        "WHERE user_id=:uid AND enrolled_at >= :since"
    ), {"uid": user_id, "since": since}) or 0)

    contacted = int(await session.scalar(text(
        "SELECT COUNT(*) FROM outreach.step_runs r "
        "JOIN outreach.enrolments e ON e.id = r.enrolment_id "
        "WHERE e.user_id=:uid AND r.status='sent' AND r.sent_at >= :since"
    ), {"uid": user_id, "since": since}) or 0)

    replied = int(await session.scalar(text(
        "SELECT COUNT(*) FROM outreach.events ev "
        "JOIN outreach.enrolments e ON e.id = ev.enrolment_id "
        "WHERE e.user_id=:uid AND ev.event_type='reply' AND ev.occurred_at >= :since"
    ), {"uid": user_id, "since": since}) or 0)

    positive_replies = int(await session.scalar(text(
        "SELECT COUNT(*) FROM outreach.events ev "
        "JOIN outreach.enrolments e ON e.id = ev.enrolment_id "
        "JOIN outreach.reply_sentiment rs ON rs.event_id = ev.id "
        "WHERE e.user_id=:uid AND ev.event_type='reply' AND rs.label IN ('positive','interested') "
        "AND ev.occurred_at >= :since"
    ), {"uid": user_id, "since": since}) or 0)

    bounced = int(await session.scalar(text(
        "SELECT COUNT(*) FROM outreach.events ev "
        "JOIN outreach.enrolments e ON e.id = ev.enrolment_id "
        "WHERE e.user_id=:uid AND ev.event_type='bounce' AND ev.occurred_at >= :since"
    ), {"uid": user_id, "since": since}) or 0)

    unsubscribed = int(await session.scalar(text(
        "SELECT COUNT(*) FROM outreach.events ev "
        "JOIN outreach.enrolments e ON e.id = ev.enrolment_id "
        "JOIN outreach.reply_sentiment rs ON rs.event_id = ev.id "
        "WHERE e.user_id=:uid AND rs.label='unsubscribe' AND ev.occurred_at >= :since"
    ), {"uid": user_id, "since": since}) or 0)

    reply_rate = (replied / contacted) if contacted else 0.0
    positive_rate = (positive_replies / replied) if replied else 0.0

    return DashboardSummary(
        period_days=days,
        enrolled=enrolled, contacted=contacted, replied=replied,
        positive_replies=positive_replies, bounced=bounced, unsubscribed=unsubscribed,
        reply_rate=reply_rate, positive_rate=positive_rate,
    )


async def sentiment_timeseries(
    session: AsyncSession, user_id: int, days: int = 30,
    bucket: Literal["day", "week"] = "day",
) -> SentimentTimeseriesResponse:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    trunc = "day" if bucket == "day" else "week"

    rows = (await session.execute(text(
        f"SELECT date_trunc('{trunc}', ev.occurred_at) AS bucket_start, rs.label, COUNT(*) "
        f"  FROM outreach.events ev "
        f"  JOIN outreach.enrolments e ON e.id = ev.enrolment_id "
        f"  JOIN outreach.reply_sentiment rs ON rs.event_id = ev.id "
        f" WHERE e.user_id=:uid AND ev.occurred_at >= :since "
        f" GROUP BY 1, 2 "
        f" ORDER BY 1"
    ), {"uid": user_id, "since": since})).all()

    by_bucket: dict[datetime, dict[str, int]] = {}
    for bucket_start, label, count in rows:
        d = by_bucket.setdefault(bucket_start, {})
        d[label] = int(count)

    points: list[SentimentBucket] = []
    for bucket_start in sorted(by_bucket.keys()):
        counts = by_bucket[bucket_start]
        points.append(SentimentBucket(
            bucket_start=bucket_start,
            positive=counts.get("positive", 0),
            interested=counts.get("interested", 0),
            objection=counts.get("objection", 0),
            negative=counts.get("negative", 0),
            unsubscribe=counts.get("unsubscribe", 0),
            auto_reply=counts.get("auto_reply", 0),
            neutral=counts.get("neutral", 0),
        ))
    return SentimentTimeseriesResponse(bucket=bucket, points=points)


async def sequences_stats(session: AsyncSession, user_id: int, days: int = 30) -> list[SequenceStats]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (await session.execute(text(
        """
        SELECT s.id, s.name, s.status,
               COALESCE(snd.cnt, 0) AS sends,
               COALESCE(rep.cnt, 0) AS replies,
               COALESCE(bnc.cnt, 0) AS bounces,
               COALESCE(pos.cnt, 0) AS positive_replies
          FROM outreach.sequences s
          LEFT JOIN (
            SELECT e.sequence_id, COUNT(*) AS cnt
              FROM outreach.step_runs r
              JOIN outreach.enrolments e ON e.id = r.enrolment_id
             WHERE e.user_id = :uid AND r.status = 'sent' AND r.sent_at >= :since
             GROUP BY e.sequence_id
          ) snd ON snd.sequence_id = s.id
          LEFT JOIN (
            SELECT e.sequence_id, COUNT(*) AS cnt
              FROM outreach.events ev
              JOIN outreach.enrolments e ON e.id = ev.enrolment_id
             WHERE e.user_id = :uid AND ev.event_type = 'reply' AND ev.occurred_at >= :since
             GROUP BY e.sequence_id
          ) rep ON rep.sequence_id = s.id
          LEFT JOIN (
            SELECT e.sequence_id, COUNT(*) AS cnt
              FROM outreach.events ev
              JOIN outreach.enrolments e ON e.id = ev.enrolment_id
             WHERE e.user_id = :uid AND ev.event_type = 'bounce' AND ev.occurred_at >= :since
             GROUP BY e.sequence_id
          ) bnc ON bnc.sequence_id = s.id
          LEFT JOIN (
            SELECT e.sequence_id, COUNT(*) AS cnt
              FROM outreach.events ev
              JOIN outreach.enrolments e ON e.id = ev.enrolment_id
              JOIN outreach.reply_sentiment rs ON rs.event_id = ev.id
             WHERE e.user_id = :uid AND ev.event_type = 'reply'
               AND rs.label IN ('positive','interested')
               AND ev.occurred_at >= :since
             GROUP BY e.sequence_id
          ) pos ON pos.sequence_id = s.id
         WHERE s.user_id = :uid
         ORDER BY COALESCE(snd.cnt, 0) DESC, s.id DESC
        """
    ), {"uid": user_id, "since": since})).all()

    out: list[SequenceStats] = []
    for sid, name, status, sends, replies, bounces, positive_replies in rows:
        out.append(SequenceStats(
            id=int(sid), name=name, status=status,
            sends=int(sends), replies=int(replies),
            bounces=int(bounces), positive_replies=int(positive_replies),
            reply_rate=(int(replies) / int(sends)) if int(sends) else 0.0,
        ))
    return out


async def hot_leads(session: AsyncSession, user_id: int, days: int = 7, limit: int = 25) -> list[HotLead]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    # Latest positive/interested sentiment per lead.
    rows = (await session.execute(text(
        """
        WITH ranked AS (
          SELECT
            e.lead_id,
            e.sequence_id,
            rs.label,
            rs.confidence,
            ev.occurred_at,
            ROW_NUMBER() OVER (PARTITION BY e.lead_id ORDER BY ev.occurred_at DESC) rn
          FROM outreach.events ev
          JOIN outreach.enrolments e ON e.id = ev.enrolment_id
          JOIN outreach.reply_sentiment rs ON rs.event_id = ev.id
          WHERE e.user_id = :uid
            AND ev.event_type = 'reply'
            AND rs.label IN ('positive', 'interested')
            AND ev.occurred_at >= :since
        )
        SELECT r.lead_id, r.sequence_id, r.label, r.confidence, r.occurred_at,
               l.email, l.first_name, l.last_name, l.company, l.title,
               s.name
          FROM ranked r
          JOIN outreach.leads l ON l.id = r.lead_id
          JOIN outreach.sequences s ON s.id = r.sequence_id
         WHERE r.rn = 1
         ORDER BY r.occurred_at DESC
         LIMIT :lim
        """
    ), {"uid": user_id, "since": since, "lim": limit})).all()

    out: list[HotLead] = []
    for (lead_id, sequence_id, label, conf, occ, email, fn, ln, company, title, seq_name) in rows:
        name = " ".join(p for p in (fn, ln) if p) or None
        out.append(HotLead(
            lead_id=int(lead_id), name=name, email=email,
            company=company, title=title,
            latest_sentiment=label, latest_confidence=float(conf),
            latest_reply_at=occ,
            sequence_id=int(sequence_id), sequence_name=seq_name,
        ))
    return out


async def at_risk(session: AsyncSession, user_id: int, hours: int = 24, limit: int = 25) -> list[AtRiskEnrolment]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (await session.execute(text(
        """
        SELECT e.id, e.lead_id, e.sequence_id, e.status,
               COALESCE(e.next_send_at, e.stopped_at, e.updated_at) AS stuck_since,
               e.stopped_reason, l.email, l.first_name, l.last_name, s.name
          FROM outreach.enrolments e
          JOIN outreach.leads l ON l.id = e.lead_id
          JOIN outreach.sequences s ON s.id = e.sequence_id
         WHERE e.user_id = :uid
           AND (
             e.status = 'errored'
             OR (e.status = 'active' AND e.next_send_at < :cutoff)
           )
         ORDER BY stuck_since ASC
         LIMIT :lim
        """
    ), {"uid": user_id, "cutoff": cutoff, "lim": limit})).all()

    out: list[AtRiskEnrolment] = []
    for (eid, lead_id, sid, status, stuck_since, reason, email, fn, ln, seq_name) in rows:
        name = " ".join(p for p in (fn, ln) if p) or None
        out.append(AtRiskEnrolment(
            enrolment_id=int(eid), lead_id=int(lead_id),
            lead_email=email, lead_name=name,
            sequence_id=int(sid), sequence_name=seq_name,
            status=status, stuck_since=stuck_since, reason=reason,
        ))
    return out

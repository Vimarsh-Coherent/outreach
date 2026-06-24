"""Heuristic spam / deliverability content score for an email (subject + body).

Pure + dependency-free: starts at 100 and subtracts weighted penalties for the
patterns that hurt cold-email deliverability — spam-trigger phrases, shouting,
punctuation spam, link-heaviness, a missing unsubscribe line, thin content, and
no personalization. Returns a 0-100 score, a verdict, and actionable findings.

This checks the TEMPLATE, so {{merge_tags}} are expected and never penalized;
their ABSENCE is what's flagged (a generic blast looks spammier).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

# Classic cold-email spam triggers (lowercased substring match).
SPAM_PHRASES = [
    "free", "100% free", "risk-free", "act now", "limited time", "buy now",
    "click here", "order now", "guarantee", "guaranteed", "no obligation",
    "winner", "congratulations", "cash", "earn money", "make money", "income",
    "cheap", "discount", "lowest price", "best price", "save big", "special promotion",
    "this is not spam", "once in a lifetime", "urgent", "apply now", "call now",
    "credit card", "increase sales", "double your", "extra income", "amazing",
]
# Acronyms that legitimately appear in CAPS.
_ALLOWED_CAPS = {"CEO", "CTO", "CFO", "COO", "VP", "AI", "API", "SaaS", "ROI", "B2B", "B2C", "USA", "UK", "OK", "FAQ", "PDF", "URL", "KPI", "CRM"}

_URL_RE = re.compile(r"https?://\S+")
_TOKEN_RE = re.compile(r"\{\{[^}]+\}\}")
_CAPS_RE = re.compile(r"\b[A-Z]{3,}\b")


@dataclass(slots=True)
class Finding:
    category: str
    severity: str       # low | medium | high
    message: str
    suggestion: str | None = None


def check(subject: str | None, body: str) -> dict:
    body = body or ""
    subject = subject or ""
    text = f"{subject}\n{body}"
    low = text.lower()
    findings: list[Finding] = []
    penalty = 0

    # 1. Spam-trigger phrases (word-boundary-ish; counts unique hits).
    hits = sorted({p for p in SPAM_PHRASES if re.search(rf"(?<![a-z]){re.escape(p)}(?![a-z])", low)})
    if hits:
        sev = "high" if len(hits) >= 4 else "medium" if len(hits) >= 2 else "low"
        penalty += min(32, len(hits) * 7)
        findings.append(Finding(
            "spam_words", sev,
            f"{len(hits)} spam-trigger phrase(s): {', '.join(hits[:6])}",
            "Rephrase or drop these — they're classic spam-filter flags.",
        ))

    # 2. SHOUTING — excessive ALL-CAPS words.
    caps = [c for c in _CAPS_RE.findall(text) if c not in _ALLOWED_CAPS]
    if len(caps) >= 2:
        penalty += min(15, len(caps) * 4)
        findings.append(Finding(
            "all_caps", "medium" if len(caps) >= 4 else "low",
            f"{len(caps)} ALL-CAPS word(s): {', '.join(caps[:5])}",
            "Use normal case — caps read as shouting to filters and people.",
        ))

    # 3. Punctuation spam (!!! / ??? / many !).
    if re.search(r"[!?]{2,}", text) or text.count("!") >= 3:
        penalty += 8
        findings.append(Finding(
            "punctuation", "low", "Excessive exclamation/question marks",
            "Keep to a single '!' at most.",
        ))

    # 4. Subject-line checks.
    if subject:
        if len(subject) > 70:
            penalty += 6
            findings.append(Finding("subject_length", "low",
                f"Subject is long ({len(subject)} chars)", "Aim for under ~50 chars."))
        letters = [c for c in subject if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
            penalty += 8
            findings.append(Finding("subject_caps", "medium",
                "Subject is mostly UPPERCASE", "Use sentence case."))
        if re.match(r"^\s*(re|fwd):", subject, re.I):
            penalty += 10
            findings.append(Finding("fake_reply", "high",
                "Deceptive 'Re:'/'Fwd:' on a cold email",
                "Don't fake a prior thread — it erodes trust and trips filters."))

    # 5. Links.
    links = _URL_RE.findall(body)
    word_count = len(body.split())
    if len(links) >= 4:
        penalty += 8
        findings.append(Finding("too_many_links", "medium",
            f"{len(links)} links in the body", "Trim to 1-2 links."))
    if links and word_count < 30:
        penalty += 6
        findings.append(Finding("link_heavy", "low",
            "Link-heavy for such a short body", "Add context text around the link."))

    # 6. Unsubscribe / opt-out (CAN-SPAM / GDPR).
    if not re.search(r"unsubscribe|opt[\s-]?out|stop receiving|no longer wish", low):
        penalty += 10
        findings.append(Finding("no_unsubscribe", "medium",
            "No unsubscribe / opt-out line",
            "Add a one-line opt-out (required by CAN-SPAM/GDPR)."))

    # 7. Personalization — a generic blast looks spammier.
    if not _TOKEN_RE.search(body):
        penalty += 8
        findings.append(Finding("no_personalization", "low",
            "No personalization tokens (e.g. {{first_name}})",
            "Add a merge tag so it isn't a generic blast."))

    # 8. Thin content.
    if word_count < 15:
        penalty += 6
        findings.append(Finding("thin_content", "low",
            f"Very short body ({word_count} words)", "A little context improves replies + deliverability."))

    score = max(0, 100 - penalty)
    verdict = "good" if score >= 80 else "warning" if score >= 60 else "poor"
    return {
        "score": score,
        "verdict": verdict,
        "findings": [asdict(f) for f in findings],
    }

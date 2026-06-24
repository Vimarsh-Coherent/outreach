"""Phase 3 — heuristic spam/deliverability content score (pure, no deps)."""
from outreach.services import spam_check as sc


def _categories(result):
    return {f["category"] for f in result["findings"]}


# ── clean message scores high ────────────────────────────────────────────────

def test_clean_personalized_email_scores_well():
    r = sc.check(
        "Quick question about {{company}}",
        "Hi {{first_name}},\n\nNoticed your team is growing — I help similar teams "
        "cut onboarding time. Worth a 15-min chat next week?\n\n"
        "Best,\n{{sender_name}}\n\nReply STOP to unsubscribe.",
    )
    assert r["score"] >= 80 and r["verdict"] == "good"
    assert r["findings"] == []


# ── individual penalties fire ────────────────────────────────────────────────

def test_spam_words_flagged():
    r = sc.check("FREE money", "Act now! Click here to buy now and earn money — 100% free guarantee.")
    assert "spam_words" in _categories(r)
    assert r["score"] < 60 and r["verdict"] == "poor"


def test_all_caps_flagged():
    r = sc.check("hello", "THIS IS A GREAT DEAL FOR YOUR TEAM {{first_name}} unsubscribe")
    assert "all_caps" in _categories(r)


def test_punctuation_spam_flagged():
    r = sc.check("hey", "Are you interested??? This is huge!!! {{first_name}} unsubscribe here")
    assert "punctuation" in _categories(r)


def test_fake_reply_subject_is_high_severity():
    r = sc.check("Re: our chat", "Hi {{first_name}}, following up. unsubscribe")
    f = next(f for f in r["findings"] if f["category"] == "fake_reply")
    assert f["severity"] == "high"


def test_missing_unsubscribe_flagged():
    r = sc.check("hi", "Hey {{first_name}}, quick note about your team. Cheers.")
    assert "no_unsubscribe" in _categories(r)


def test_no_personalization_flagged():
    r = sc.check("hi", "Hey there, quick note about your team. Reply STOP to unsubscribe. "
                       "We work with lots of teams on this and would love to talk soon.")
    assert "no_personalization" in _categories(r)


def test_too_many_links_flagged():
    body = "see " + " ".join(f"https://x.com/{i}" for i in range(5)) + " {{first_name}} unsubscribe"
    r = sc.check("hi", body)
    assert "too_many_links" in _categories(r)


def test_thin_content_flagged():
    r = sc.check("hi", "{{first_name}} unsubscribe")
    assert "thin_content" in _categories(r)


def test_allowed_acronyms_not_flagged_as_caps():
    r = sc.check("hi", "Hi {{first_name}}, your CEO and CTO will love our API + CRM ROI. unsubscribe")
    assert "all_caps" not in _categories(r)


# ── score bounds + verdict mapping ───────────────────────────────────────────

def test_score_clamped_and_verdict_thresholds():
    worst = sc.check("RE: FREE CASH!!!", "BUY NOW!!! CLICK HERE!!! 100% FREE GUARANTEE EARN MONEY")
    assert 0 <= worst["score"] <= 100
    assert worst["verdict"] == "poor"
    good = sc.check("Question about {{company}}",
                    "Hi {{first_name}}, short note with context here. Reply to opt-out anytime. Thanks.")
    assert good["verdict"] in ("good", "warning")

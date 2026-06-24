"""Phase 2 — A/B testing core (pure logic, no DB).

Variant validation, deterministic weighted selection (stable per enrolment,
weight-proportional across leads), message resolution, and winner suggestion.
"""
from outreach.services import ab_testing as ab


# ── normalize_variants ───────────────────────────────────────────────────────

def test_normalize_requires_two_usable_variants():
    assert ab.normalize_variants([{"body": "only one"}]) == []          # <2 → not an A/B
    assert ab.normalize_variants("nope") == []                          # not a list
    assert ab.normalize_variants(None) == []


def test_normalize_skips_bodyless_and_defaults_labels_weights():
    out = ab.normalize_variants([
        {"subject": "S1", "body": "hi A"},
        {"body": "   "},                       # blank body → dropped
        {"label": "Z", "body": "hi C", "weight": 5},
    ])
    # two valid (the blank one dropped)
    assert [v["label"] for v in out] == ["A", "Z"]   # first defaults to "A"
    assert out[0]["weight"] == 1 and out[1]["weight"] == 5
    assert out[0]["subject"] == "S1" and out[1]["subject"] is None


def test_normalize_clamps_weight():
    out = ab.normalize_variants([
        {"body": "a", "weight": 0}, {"body": "b", "weight": 99999},
    ])
    assert out[0]["weight"] == 1 and out[1]["weight"] == 1000


# ── pick_variant ─────────────────────────────────────────────────────────────

VARIANTS = [
    {"label": "A", "subject": "sa", "body": "ba", "weight": 1},
    {"label": "B", "subject": "sb", "body": "bb", "weight": 1},
]


def test_pick_is_deterministic_per_seed():
    a = ab.pick_variant(VARIANTS, "enrol:42:step:7")
    b = ab.pick_variant(VARIANTS, "enrol:42:step:7")
    assert a["label"] == b["label"]  # same lead always gets the same variant


def test_pick_distribution_honors_weights():
    weighted = [
        {"label": "A", "body": "a", "weight": 3},
        {"label": "B", "body": "b", "weight": 1},
    ]
    counts = {"A": 0, "B": 0}
    for i in range(2000):
        counts[ab.pick_variant(weighted, f"seed-{i}")["label"]] += 1
    # ~75/25 split; allow generous tolerance for hashing noise
    assert 0.68 < counts["A"] / 2000 < 0.82


def test_pick_both_variants_appear():
    seen = {ab.pick_variant(VARIANTS, f"s{i}")["label"] for i in range(50)}
    assert seen == {"A", "B"}


# ── resolve_message ──────────────────────────────────────────────────────────

def test_resolve_no_variants_uses_defaults():
    r = ab.resolve_message(step_config={}, default_subject="DS", default_body="DB", seed="x")
    assert r.subject == "DS" and r.body == "DB" and r.variant_label is None


def test_resolve_uses_variant_body_and_label():
    cfg = {"variants": [
        {"label": "A", "subject": "SA", "body": "BA"},
        {"label": "B", "subject": "SB", "body": "BB"},
    ]}
    r = ab.resolve_message(step_config=cfg, default_subject="DS", default_body="DB", seed="lead-1")
    assert r.variant_label in {"A", "B"}
    assert r.body in {"BA", "BB"} and r.subject in {"SA", "SB"}


def test_resolve_variant_without_subject_falls_back_to_default():
    cfg = {"variants": [
        {"label": "A", "body": "BA"},   # no subject
        {"label": "B", "body": "BB"},
    ]}
    r = ab.resolve_message(step_config=cfg, default_subject="DEFAULT", default_body="DB", seed="k")
    assert r.subject == "DEFAULT"


# ── pick_winner ──────────────────────────────────────────────────────────────

def test_winner_none_until_min_sample():
    stats = [
        {"label": "A", "sent": 5, "reply_rate": 50.0, "positive_rate": 10.0, "open_rate": 1},
        {"label": "B", "sent": 5, "reply_rate": 10.0, "positive_rate": 1.0, "open_rate": 1},
    ]
    assert ab.pick_winner(stats, min_sample=20) is None


def test_winner_picks_highest_reply_rate():
    stats = [
        {"label": "A", "sent": 50, "reply_rate": 8.0,  "positive_rate": 2.0, "open_rate": 40.0},
        {"label": "B", "sent": 50, "reply_rate": 12.0, "positive_rate": 5.0, "open_rate": 35.0},
    ]
    assert ab.pick_winner(stats, min_sample=20) == "B"

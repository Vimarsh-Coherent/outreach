"""Phase 5 — conditional branching engine (pure logic)."""
from outreach.services import branching as br


# ── normalize_transitions ────────────────────────────────────────────────────

def test_normalize_keeps_valid_drops_unknown():
    out = br.normalize_transitions([
        {"on": "replied", "to_step_id": 5},
        {"on": "bogus", "to_step_id": 1},      # unknown condition → dropped
        {"on": "default", "to_step_id": None},  # stop
        "nope",
    ])
    assert out == [
        {"on": "replied", "to_step_id": 5},
        {"on": "default", "to_step_id": None},
    ]


def test_normalize_coerces_string_id_and_guards_bool():
    out = br.normalize_transitions([
        {"on": "opened", "to_step_id": "7"},
        {"on": "clicked", "to_step_id": True},   # bool is not a real id
    ])
    assert out[0]["to_step_id"] == 7
    assert out[1]["to_step_id"] is None


def test_normalize_non_list():
    assert br.normalize_transitions(None) == []
    assert br.normalize_transitions({"on": "replied"}) == []


# ── needs_branch_wait ────────────────────────────────────────────────────────

def test_needs_wait_only_for_event_conditions():
    assert br.needs_branch_wait([{"on": "replied", "to_step_id": 2}]) is True
    assert br.needs_branch_wait([{"on": "default", "to_step_id": 2}]) is False
    assert br.needs_branch_wait([]) is False


# ── evaluate ─────────────────────────────────────────────────────────────────

T = [
    {"on": "replied", "to_step_id": 10},
    {"on": "opened", "to_step_id": 20},
    {"on": "default", "to_step_id": 30},
]


def test_evaluate_first_match_wins_replied():
    assert br.evaluate(T, replied=True, opened=True, clicked=False) == ("step", 10)


def test_evaluate_second_match_when_first_false():
    assert br.evaluate(T, replied=False, opened=True, clicked=False) == ("step", 20)


def test_evaluate_falls_to_default():
    assert br.evaluate(T, replied=False, opened=False, clicked=False) == ("step", 30)


def test_evaluate_stop_when_to_step_none():
    t = [{"on": "replied", "to_step_id": None}, {"on": "default", "to_step_id": 9}]
    assert br.evaluate(t, replied=True, opened=False, clicked=False) == ("stop", None)


def test_evaluate_continue_when_no_match_and_no_default():
    t = [{"on": "replied", "to_step_id": 10}]
    assert br.evaluate(t, replied=False, opened=False, clicked=False) == ("continue", None)


def test_evaluate_empty_is_continue():
    assert br.evaluate([], replied=True, opened=True, clicked=True) == ("continue", None)


def test_evaluate_clicked_condition():
    t = [{"on": "clicked", "to_step_id": 5}, {"on": "default", "to_step_id": 6}]
    assert br.evaluate(t, replied=False, opened=False, clicked=True) == ("step", 5)
    assert br.evaluate(t, replied=False, opened=False, clicked=False) == ("step", 6)

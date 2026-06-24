"""Conditional branching engine (pure).

A step's ``transitions`` is an ordered list of edges. After the step is sent (and
a wait window for events elapses), the FIRST matching edge decides the next node:

  {"on": "replied"|"opened"|"clicked"|"default", "to_step_id": int|null}

- "default" always matches (catch-all / else path).
- to_step_id == null  → stop the sequence on that branch.
- No edge matches      → fall back to the linear next step (backward compatible).

Empty transitions ⇒ purely linear (the existing behavior).
"""
from __future__ import annotations

VALID_CONDITIONS = {"replied", "opened", "clicked", "default"}

# Conditions that require waiting for an inbound signal (vs "default" which is
# immediate). Used to decide whether a step needs a branch-wait at all.
EVENT_CONDITIONS = {"replied", "opened", "clicked"}


def normalize_transitions(raw: object) -> list[dict]:
    """Validate + clean a step's transitions. Drops malformed/unknown edges."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        on = t.get("on")
        if on not in VALID_CONDITIONS:
            continue
        to = t.get("to_step_id")
        if isinstance(to, bool):  # guard: bool is an int subclass
            to_id = None
        elif isinstance(to, int):
            to_id = to
        elif isinstance(to, str) and to.isdigit():
            to_id = int(to)
        else:
            to_id = None
        out.append({"on": on, "to_step_id": to_id})
    return out


def needs_branch_wait(transitions: list[dict]) -> bool:
    """True if any edge depends on an inbound event (so we should wait before
    evaluating). Pure 'default' transitions don't need a wait."""
    return any(t["on"] in EVENT_CONDITIONS for t in transitions)


def evaluate(
    transitions: list[dict], *, replied: bool, opened: bool, clicked: bool
) -> tuple[str, int | None]:
    """Pick the branch. Returns:
      ("step", id)   → go to step id
      ("stop", None) → end the sequence
      ("continue", None) → no edge matched → linear fallback
    """
    cond = {"replied": replied, "opened": opened, "clicked": clicked}
    for t in transitions:
        on = t["on"]
        if on == "default" or cond.get(on, False):
            return ("stop", None) if t["to_step_id"] is None else ("step", t["to_step_id"])
    return ("continue", None)

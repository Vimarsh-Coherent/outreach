"""Pure helpers for sequence step reorder delay remapping."""

from __future__ import annotations


def remap_steps_with_transition_delays(
    old_ordered: list[tuple[int, int, int]],
    new_step_ids: list[int],
) -> list[tuple[int, int, int, int]]:
    """Remap step order and transition delays.

    ``old_ordered`` is ``(step_id, delay_days, delay_hours)`` sorted by old step_order.
    Returns ``(step_id, new_step_order, delay_days, delay_hours)`` for each step.
    """
    if len(old_ordered) != len(new_step_ids):
        raise ValueError("reorder list must contain every step exactly once")
    if {s[0] for s in old_ordered} != set(new_step_ids):
        raise ValueError("reorder list must contain every step exactly once")

    transition_delays = [(d, h) for _, d, h in old_ordered[1:]]
    result: list[tuple[int, int, int, int]] = []
    for new_order, sid in enumerate(new_step_ids, start=1):
        if new_order == 1:
            delay_days, delay_hours = 0, 0
        else:
            delay_days, delay_hours = transition_delays[new_order - 2]
        result.append((sid, new_order, delay_days, delay_hours))
    return result

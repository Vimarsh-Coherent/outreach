"""Tests for transition-based delay remapping on step reorder."""

from outreach.services.step_reorder import remap_steps_with_transition_delays


def test_linkedin_before_email_three_steps():
    # Email(0), LinkedIn(2d), Message(1d)
    old = [(1, 0, 0), (2, 2, 0), (3, 0, 1)]
    result = remap_steps_with_transition_delays(old, [2, 1, 3])
    assert result == [(2, 1, 0, 0), (1, 2, 2, 0), (3, 3, 0, 1)]


def test_message_to_top_three_steps():
    # Email(0), Call(3d), Message(1d)
    old = [(1, 0, 0), (2, 3, 0), (3, 0, 1)]
    result = remap_steps_with_transition_delays(old, [3, 1, 2])
    assert result == [(3, 1, 0, 0), (1, 2, 3, 0), (2, 3, 0, 1)]


def test_sms_to_top_four_steps():
    # Email(0), Call(3d), DM(1d), SMS(4d)
    old = [(1, 0, 0), (2, 3, 0), (3, 0, 1), (4, 4, 0)]
    result = remap_steps_with_transition_delays(old, [4, 1, 2, 3])
    assert result == [(4, 1, 0, 0), (1, 2, 3, 0), (2, 3, 0, 1), (3, 4, 4, 0)]


def test_step_numbers_renumber_one_to_n():
    old = [(10, 0, 0), (20, 2, 0), (30, 1, 0), (40, 4, 0)]
    result = remap_steps_with_transition_delays(old, [30, 10, 40, 20])
    orders = [r[1] for r in result]
    assert orders == [1, 2, 3, 4]


def test_delays_follow_positions_not_step_ids():
    # Call had 3d at position 2; after moving to position 4 it should get 4d (slot 4 delay)
    old = [(1, 0, 0), (2, 3, 0), (3, 0, 1), (4, 4, 0)]
    result = remap_steps_with_transition_delays(old, [1, 3, 4, 2])
    by_id = {sid: (order, d, h) for sid, order, d, h in result}
    assert by_id[2] == (4, 4, 0)  # Call moved to slot 4, gets transition slot 4 delay

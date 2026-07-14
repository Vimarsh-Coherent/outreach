"""Self-echo guard: the IMAP poller must never treat the outreach user's own
outbound copy (Gmail mirrors sent mail into All Mail) as an inbound reply."""
from outreach.workers.imap_poller import is_self_echo

SELF = {"naman567lol@gmail.com", "sender@corp.com"}


def test_own_address_is_self_echo():
    assert is_self_echo("naman567lol@gmail.com", SELF) is True


def test_case_and_whitespace_insensitive():
    assert is_self_echo("  Naman567LOL@Gmail.com  ", SELF) is True


def test_second_channel_address():
    assert is_self_echo("sender@corp.com", SELF) is True


def test_real_lead_reply_is_not_self_echo():
    assert is_self_echo("lead@prospect.com", SELF) is False


def test_none_and_empty_are_not_self_echo():
    assert is_self_echo(None, SELF) is False
    assert is_self_echo("", SELF) is False


def test_empty_self_set_never_matches():
    assert is_self_echo("anyone@x.com", set()) is False

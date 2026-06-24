"""Phase 1 — email open/click tracking.

Covers the pure token + HTML-rendering layer (no DB): HMAC tokens are
kind-bound and tamper-proof, links get rewritten through the signed redirect,
and the open pixel is appended only when enabled.
"""
from outreach.services import email_tracking as et


# ── token sign / verify ──────────────────────────────────────────────────────

def test_token_roundtrip_open_and_click():
    assert et.parse_token(et.make_token(42, "o"), "o") == 42
    assert et.parse_token(et.make_token(42, "c"), "c") == 42


def test_token_kind_is_bound_not_interchangeable():
    # An open token must NOT verify as a click token (and vice-versa).
    open_tok = et.make_token(7, "o")
    assert et.parse_token(open_tok, "c") is None
    click_tok = et.make_token(7, "c")
    assert et.parse_token(click_tok, "o") is None


def test_token_tamper_rejected():
    tok = et.make_token(100, "o")
    id_part, sig = tok.rsplit(".", 1)
    assert et.parse_token(f"999.{sig}", "o") is None       # wrong id
    assert et.parse_token(f"{id_part}.deadbeef", "o") is None  # wrong sig


def test_token_malformed_returns_none():
    for bad in ["", "nodot", "abc.def", ".", "12."]:
        assert et.parse_token(bad, "o") is None


def test_b64url_roundtrip_with_query_and_unicode():
    for url in [
        "https://example.com/a?b=1&c=2#frag",
        "https://例え.test/路径?q=café",
    ]:
        assert et.b64url_decode(et.b64url_encode(url)) == url


# ── tracked HTML rendering ───────────────────────────────────────────────────

BASE = "https://track.example.com"


def test_pixel_only_when_track_opens():
    html_on = et.build_tracked_html("hi", step_run_id=1, base_url=BASE, track_opens=True, track_clicks=False)
    assert f"{BASE}/t/o/" in html_on and 'width="1"' in html_on
    html_off = et.build_tracked_html("hi", step_run_id=1, base_url=BASE, track_opens=False, track_clicks=False)
    assert "/t/o/" not in html_off


def test_links_rewritten_when_track_clicks():
    body = "See https://acme.com/pricing for details."
    html = et.build_tracked_html(body, step_run_id=5, base_url=BASE, track_opens=False, track_clicks=True)
    assert f"{BASE}/t/c/" in html
    # original url is preserved (b64-encoded) in the redirect target
    enc = et.b64url_encode("https://acme.com/pricing")
    assert f"u={enc}" in html
    # anchor text still shows the human-readable URL
    assert ">https://acme.com/pricing</a>" in html


def test_links_not_rewritten_when_clicks_off():
    body = "go to https://acme.com now"
    html = et.build_tracked_html(body, step_run_id=5, base_url=BASE, track_opens=False, track_clicks=False)
    assert "/t/c/" not in html
    assert 'href="https://acme.com"' in html  # plain anchor, not tracked


def test_disabled_when_base_url_empty():
    # No public base URL → no pixel and no click rewrite even if flags are on.
    html = et.build_tracked_html("x https://acme.com", step_run_id=1, base_url="", track_opens=True, track_clicks=True)
    assert "/t/o/" not in html and "/t/c/" not in html


def test_html_escaping_prevents_injection():
    body = "<script>alert(1)</script> & 'quotes'"
    html = et.build_tracked_html(body, step_run_id=1, base_url=BASE, track_opens=False, track_clicks=False)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html and "&amp;" in html


def test_multiple_links_each_tracked():
    body = "a https://one.com b https://two.com c"
    html = et.build_tracked_html(body, step_run_id=9, base_url=BASE, track_opens=False, track_clicks=True)
    assert html.count(f"{BASE}/t/c/") == 2
    assert f"u={et.b64url_encode('https://one.com')}" in html
    assert f"u={et.b64url_encode('https://two.com')}" in html


def test_newlines_become_breaks():
    html = et.build_tracked_html("line1\nline2", step_run_id=1, base_url=BASE, track_opens=False, track_clicks=False)
    assert "<br>" in html


def test_pixel_gif_is_a_valid_gif():
    g = et.pixel_gif()
    assert g[:6] in (b"GIF87a", b"GIF89a") and len(g) < 100

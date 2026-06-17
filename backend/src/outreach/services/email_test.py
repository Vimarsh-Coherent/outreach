import ssl
import time
from email.message import EmailMessage

import aioimaplib
import aiosmtplib

from outreach.schemas.channels import (
    IMAPConfig,
    SMTPConfig,
    TestEmailChannelRequest,
    TestEmailChannelResponse,
    TestStepResult,
)


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


async def _smtp_test(cfg: SMTPConfig, probe_to: str | None) -> tuple[TestStepResult, TestStepResult, TestStepResult | None]:
    use_tls = cfg.security == "ssl_tls"
    use_starttls = cfg.security == "starttls"

    client = aiosmtplib.SMTP(
        hostname=cfg.host,
        port=cfg.port,
        use_tls=use_tls,
        start_tls=False,
        tls_context=_ssl_ctx() if use_tls or use_starttls else None,
        timeout=cfg.timeout_seconds,
    )

    t0 = time.monotonic()
    try:
        await client.connect()
    except Exception as e:  # noqa: BLE001
        return (
            TestStepResult(ok=False, detail=f"connect failed: {type(e).__name__}: {e}"),
            TestStepResult(ok=False, detail="skipped (connect failed)"),
            None,
        )
    connect_ms = int((time.monotonic() - t0) * 1000)

    if use_starttls:
        try:
            await client.starttls(tls_context=_ssl_ctx())
        except Exception as e:  # noqa: BLE001
            await client.quit()
            return (
                TestStepResult(ok=False, detail=f"STARTTLS failed: {type(e).__name__}: {e}", latency_ms=connect_ms),
                TestStepResult(ok=False, detail="skipped"),
                None,
            )

    if not cfg.username:
        # No-auth relay: skip login.
        return (
            TestStepResult(ok=True, detail=f"connected ({cfg.host}:{cfg.port})", latency_ms=connect_ms),
            TestStepResult(ok=True, detail="skipped (no username -> no AUTH)"),
            None,
        )

    t1 = time.monotonic()
    try:
        await client.login(cfg.username, cfg.password)
    except aiosmtplib.SMTPAuthenticationError as e:
        await client.quit()
        return (
            TestStepResult(ok=True, detail=f"connected ({cfg.host}:{cfg.port})", latency_ms=connect_ms),
            TestStepResult(ok=False, detail=f"AUTH rejected: {e.code} {e.message}"),
            None,
        )
    except Exception as e:  # noqa: BLE001
        await client.quit()
        return (
            TestStepResult(ok=True, detail=f"connected ({cfg.host}:{cfg.port})", latency_ms=connect_ms),
            TestStepResult(ok=False, detail=f"AUTH error: {type(e).__name__}: {e}"),
            None,
        )
    auth_ms = int((time.monotonic() - t1) * 1000)

    probe_result: TestStepResult | None = None
    if probe_to:
        msg = EmailMessage()
        from_header = f"{cfg.from_name} <{cfg.from_email}>" if cfg.from_name else cfg.from_email
        msg["From"] = from_header
        msg["To"] = probe_to
        msg["Subject"] = "Coherent Outreach — channel test"
        msg.set_content(
            "This is a test message from your Coherent Outreach SMTP channel.\n"
            "If you received it, your SMTP configuration is working correctly."
        )
        t2 = time.monotonic()
        try:
            await client.send_message(msg)
            probe_result = TestStepResult(
                ok=True,
                detail=f"probe sent to {probe_to}",
                latency_ms=int((time.monotonic() - t2) * 1000),
            )
        except Exception as e:  # noqa: BLE001
            probe_result = TestStepResult(ok=False, detail=f"send failed: {type(e).__name__}: {e}")

    await client.quit()
    return (
        TestStepResult(ok=True, detail=f"connected ({cfg.host}:{cfg.port})", latency_ms=connect_ms),
        TestStepResult(ok=True, detail=f"authenticated as {cfg.username}", latency_ms=auth_ms),
        probe_result,
    )


async def _imap_test(cfg: IMAPConfig) -> tuple[TestStepResult, TestStepResult]:
    use_ssl = cfg.security == "ssl_tls"
    use_starttls = cfg.security == "starttls"

    t0 = time.monotonic()
    client = aioimaplib.IMAP4_SSL(host=cfg.host, port=cfg.port, timeout=cfg.timeout_seconds) if use_ssl else aioimaplib.IMAP4(
        host=cfg.host, port=cfg.port, timeout=cfg.timeout_seconds
    )
    try:
        await client.wait_hello_from_server()
    except Exception as e:  # noqa: BLE001
        return (
            TestStepResult(ok=False, detail=f"connect failed: {type(e).__name__}: {e}"),
            TestStepResult(ok=False, detail="skipped"),
        )
    connect_ms = int((time.monotonic() - t0) * 1000)

    if use_starttls:
        try:
            r = await client.starttls()
            if r.result != "OK":
                return (
                    TestStepResult(ok=False, detail=f"STARTTLS rejected: {r.result}", latency_ms=connect_ms),
                    TestStepResult(ok=False, detail="skipped"),
                )
        except Exception as e:  # noqa: BLE001
            return (
                TestStepResult(ok=False, detail=f"STARTTLS error: {type(e).__name__}: {e}", latency_ms=connect_ms),
                TestStepResult(ok=False, detail="skipped"),
            )

    t1 = time.monotonic()
    try:
        r = await client.login(cfg.username, cfg.password)
    except Exception as e:  # noqa: BLE001
        try:
            await client.logout()
        except Exception:  # noqa: BLE001, S110
            pass
        return (
            TestStepResult(ok=True, detail=f"connected ({cfg.host}:{cfg.port})", latency_ms=connect_ms),
            TestStepResult(ok=False, detail=f"AUTH error: {type(e).__name__}: {e}"),
        )

    if r.result != "OK":
        try:
            await client.logout()
        except Exception:  # noqa: BLE001, S110
            pass
        return (
            TestStepResult(ok=True, detail=f"connected ({cfg.host}:{cfg.port})", latency_ms=connect_ms),
            TestStepResult(ok=False, detail=f"AUTH rejected: {r.result}"),
        )

    auth_ms = int((time.monotonic() - t1) * 1000)
    try:
        await client.logout()
    except Exception:  # noqa: BLE001, S110
        pass

    return (
        TestStepResult(ok=True, detail=f"connected ({cfg.host}:{cfg.port})", latency_ms=connect_ms),
        TestStepResult(ok=True, detail=f"authenticated as {cfg.username}", latency_ms=auth_ms),
    )


async def test_email_channel(req: TestEmailChannelRequest) -> TestEmailChannelResponse:
    smtp_connect, smtp_auth, probe = await _smtp_test(req.smtp, req.send_probe_to)
    imap_connect: TestStepResult | None = None
    imap_auth: TestStepResult | None = None
    if req.imap is not None:
        imap_connect, imap_auth = await _imap_test(req.imap)
    return TestEmailChannelResponse(
        smtp_connect=smtp_connect,
        smtp_auth=smtp_auth,
        smtp_probe_send=probe,
        imap_connect=imap_connect,
        imap_auth=imap_auth,
    )

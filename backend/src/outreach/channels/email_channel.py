import ssl
from dataclasses import dataclass
from email.message import EmailMessage

import aiosmtplib

from outreach.schemas.channels import SMTPConfig


@dataclass(slots=True)
class SendResult:
    ok: bool
    error: str | None = None


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


async def send_email(
    *,
    cfg: SMTPConfig,
    to_address: str,
    subject: str,
    body: str,
    message_id: str,
    in_reply_to: str | None = None,
    references: list[str] | None = None,
    headers: dict[str, str] | None = None,
) -> SendResult:
    use_tls = cfg.security == "ssl_tls"
    use_starttls = cfg.security == "starttls"

    msg = EmailMessage()
    from_header = f"{cfg.from_name} <{cfg.from_email}>" if cfg.from_name else cfg.from_email
    msg["From"] = from_header
    msg["To"] = to_address
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = " ".join(references)
    if headers:
        for k, v in headers.items():
            msg[k] = v
    msg.set_content(body)

    client = aiosmtplib.SMTP(
        hostname=cfg.host,
        port=cfg.port,
        use_tls=use_tls,
        start_tls=False,
        tls_context=_ssl_ctx() if use_tls or use_starttls else None,
        timeout=cfg.timeout_seconds,
    )
    try:
        await client.connect()
        if use_starttls:
            await client.starttls(tls_context=_ssl_ctx())
        if cfg.username:
            await client.login(cfg.username, cfg.password)
        await client.send_message(msg)
    except aiosmtplib.SMTPAuthenticationError as e:
        return SendResult(ok=False, error=f"AUTH {e.code}: {e.message}")
    except Exception as e:  # noqa: BLE001
        return SendResult(ok=False, error=f"{type(e).__name__}: {e}")
    finally:
        try:
            await client.quit()
        except Exception:  # noqa: BLE001, S110
            pass
    return SendResult(ok=True)

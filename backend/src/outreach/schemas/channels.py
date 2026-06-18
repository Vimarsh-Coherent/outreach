from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, model_validator

SecurityMode = Literal["ssl_tls", "starttls", "none"]


class SMTPConfig(BaseModel):
    host: str = Field(min_length=1, max_length=200)
    port: int = Field(ge=1, le=65535)
    security: SecurityMode = "starttls"
    # Empty username/password = no AUTH (used for IP-allowlisted internal relays
    # and the smoke-test fake server).
    username: str = Field(default="", max_length=200)
    password: str = Field(default="", max_length=500)
    from_email: EmailStr
    from_name: str | None = Field(default=None, max_length=200)
    timeout_seconds: int = Field(default=30, ge=5, le=120)


class IMAPConfig(BaseModel):
    host: str = Field(min_length=1, max_length=200)
    port: int = Field(ge=1, le=65535)
    security: SecurityMode = "ssl_tls"
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)
    mailbox: str = "INBOX"
    timeout_seconds: int = Field(default=30, ge=5, le=120)


class EmailChannelCreate(BaseModel):
    display_label: str = Field(min_length=1, max_length=120)
    smtp: SMTPConfig
    imap: IMAPConfig | None = None
    daily_cap: int = Field(default=100, ge=1, le=5000)

    @model_validator(mode="after")
    def _imap_username_default(self) -> "EmailChannelCreate":
        # If IMAP is provided but its username/password are empty, fall back to SMTP creds.
        if self.imap is not None:
            if not self.imap.username:
                self.imap.username = self.smtp.username
            if not self.imap.password:
                self.imap.password = self.smtp.password
        return self


class WhatsAppChannelCreate(BaseModel):
    display_label: str = Field(min_length=1, max_length=120)
    daily_cap: int = Field(default=100, ge=1, le=2000)


class WhatsAppStatusOut(BaseModel):
    # Mirrors the sidecar's /getConnectionState, plus whether a platform channel
    # row exists. state ∈ starting|qr|connected|disconnected|logged_out|unavailable
    state: str
    connected: bool
    me: str | None = None
    channel_id: int | None = None


class WhatsAppQrOut(BaseModel):
    state: str
    qr: str | None = None  # PNG data URL while pairing, else null


class TestEmailChannelRequest(BaseModel):
    smtp: SMTPConfig
    imap: IMAPConfig | None = None
    send_probe_to: EmailStr | None = None


class TestStepResult(BaseModel):
    ok: bool
    detail: str
    latency_ms: int | None = None


class TestEmailChannelResponse(BaseModel):
    smtp_connect: TestStepResult
    smtp_auth: TestStepResult
    smtp_probe_send: TestStepResult | None = None
    imap_connect: TestStepResult | None = None
    imap_auth: TestStepResult | None = None


class ChannelOut(BaseModel):
    id: int
    channel_type: str
    display_label: str
    status: str
    daily_cap: int
    sent_today: int
    created_at: datetime
    updated_at: datetime
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_from_email: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None


class EmailPreset(BaseModel):
    key: str
    label: str
    smtp_host: str
    smtp_port: int
    smtp_security: SecurityMode
    imap_host: str
    imap_port: int
    imap_security: SecurityMode
    notes: str | None = None


EMAIL_PRESETS: list[EmailPreset] = [
    EmailPreset(
        key="gmail",
        label="Gmail / Google Workspace",
        smtp_host="smtp.gmail.com", smtp_port=587, smtp_security="starttls",
        imap_host="imap.gmail.com", imap_port=993, imap_security="ssl_tls",
        notes="Requires a 16-char App Password (Google Account → Security → 2-Step Verification → App passwords). Regular Google password will NOT work.",
    ),
    EmailPreset(
        key="outlook_personal",
        label="Outlook.com / Hotmail / Live",
        smtp_host="smtp-mail.outlook.com", smtp_port=587, smtp_security="starttls",
        imap_host="outlook.office365.com", imap_port=993, imap_security="ssl_tls",
        notes="Microsoft is phasing out basic auth on consumer Outlook accounts — if AUTH fails, you may need an OAuth integration (not in v1) or to use a paid M365 mailbox.",
    ),
    EmailPreset(
        key="microsoft_365",
        label="Microsoft 365 / Exchange Online",
        smtp_host="smtp.office365.com", smtp_port=587, smtp_security="starttls",
        imap_host="outlook.office365.com", imap_port=993, imap_security="ssl_tls",
        notes="Your tenant admin must have SMTP AUTH enabled for the mailbox. If you have MFA, generate an app password.",
    ),
    EmailPreset(
        key="yahoo",
        label="Yahoo Mail",
        smtp_host="smtp.mail.yahoo.com", smtp_port=465, smtp_security="ssl_tls",
        imap_host="imap.mail.yahoo.com", imap_port=993, imap_security="ssl_tls",
        notes="Requires an App Password from Yahoo Account Security.",
    ),
    EmailPreset(
        key="zoho",
        label="Zoho Mail",
        smtp_host="smtp.zoho.com", smtp_port=465, smtp_security="ssl_tls",
        imap_host="imap.zoho.com", imap_port=993, imap_security="ssl_tls",
        notes="EU users: use smtp.zoho.eu / imap.zoho.eu instead.",
    ),
    EmailPreset(
        key="zoho_eu",
        label="Zoho Mail (EU)",
        smtp_host="smtp.zoho.eu", smtp_port=465, smtp_security="ssl_tls",
        imap_host="imap.zoho.eu", imap_port=993, imap_security="ssl_tls",
    ),
    EmailPreset(
        key="icloud",
        label="iCloud Mail",
        smtp_host="smtp.mail.me.com", smtp_port=587, smtp_security="starttls",
        imap_host="imap.mail.me.com", imap_port=993, imap_security="ssl_tls",
        notes="Requires an App-Specific Password from appleid.apple.com.",
    ),
    EmailPreset(
        key="fastmail",
        label="Fastmail",
        smtp_host="smtp.fastmail.com", smtp_port=465, smtp_security="ssl_tls",
        imap_host="imap.fastmail.com", imap_port=993, imap_security="ssl_tls",
        notes="Requires an App Password from Fastmail → Settings → Privacy & Security.",
    ),
    EmailPreset(
        key="protonmail_bridge",
        label="ProtonMail (via Bridge)",
        smtp_host="127.0.0.1", smtp_port=1025, smtp_security="starttls",
        imap_host="127.0.0.1", imap_port=1143, imap_security="starttls",
        notes="Requires the ProtonMail Bridge app running locally; uses the Bridge-generated password (NOT your Proton account password).",
    ),
    EmailPreset(
        key="brevo",
        label="Brevo (Sendinblue)",
        smtp_host="smtp-relay.brevo.com", smtp_port=587, smtp_security="starttls",
        imap_host="", imap_port=0, imap_security="ssl_tls",
        notes=(
            "Username = your Brevo account login email. "
            "Password = an SMTP key generated in Brevo → Settings → SMTP & API → SMTP tab → 'Generate a new SMTP key' "
            "(NOT your account password). Free tier sends up to 300 emails/day with a Brevo footer; paid tiers remove it. "
            "Brevo has no IMAP — for reply detection, configure IMAP against the From address's actual mailbox "
            "(e.g. your Gmail/Outlook) using the IMAP fields below."
        ),
    ),
    EmailPreset(
        key="sendgrid",
        label="SendGrid (transactional)",
        smtp_host="smtp.sendgrid.net", smtp_port=587, smtp_security="starttls",
        imap_host="", imap_port=0, imap_security="ssl_tls",
        notes="Username is the literal string 'apikey'; password is your SendGrid API key. SendGrid has no IMAP — reply detection must use a different mailbox.",
    ),
    EmailPreset(
        key="mailgun",
        label="Mailgun (transactional)",
        smtp_host="smtp.mailgun.org", smtp_port=587, smtp_security="starttls",
        imap_host="", imap_port=0, imap_security="ssl_tls",
        notes="Mailgun has no IMAP — replies arrive via webhook. v1 does not handle Mailgun replies; use a separate IMAP mailbox if needed.",
    ),
    EmailPreset(
        key="amazon_ses",
        label="Amazon SES (transactional)",
        smtp_host="email-smtp.us-east-1.amazonaws.com", smtp_port=587, smtp_security="starttls",
        imap_host="", imap_port=0, imap_security="ssl_tls",
        notes="Username + password are SES SMTP credentials (generated in the SES console — NOT your AWS access key). Update the region prefix as needed.",
    ),
    EmailPreset(
        key="custom",
        label="Custom / other provider",
        smtp_host="", smtp_port=587, smtp_security="starttls",
        imap_host="", imap_port=993, imap_security="ssl_tls",
        notes="Bring your own host/port/security.",
    ),
]

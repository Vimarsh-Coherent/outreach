import asyncio, logging, ssl
logging.basicConfig(level=logging.WARNING)
import aioimaplib
from outreach.db import SessionLocal
from outreach.models.channel import Channel
from outreach.utils.crypto import decrypt_json
from outreach.schemas.channels import IMAPConfig
from outreach.services.email_parse import parse_inbound, NOREPLY_FROM_RX
from outreach.workers.imap_poller import _match_reply_by_sender, _imap_date_since
from sqlalchemy import select

async def main():
    async with SessionLocal() as session:
        channels = (await session.execute(
            select(Channel).where(Channel.channel_type == "email", Channel.status == "active")
        )).scalars().all()
    ch = channels[0]
    cfg = IMAPConfig.model_validate(decrypt_json(ch.config_encrypted)["imap"])
    ctx = ssl.create_default_context()
    client = aioimaplib.IMAP4_SSL(host=cfg.host, port=cfg.port, ssl_context=ctx)
    await client.wait_hello_from_server()
    await client.login(cfg.username, cfg.password)

    sel = await client.select('"[Gmail]/All Mail"')
    print("Select All Mail:", sel.result)

    r = await client.search(f"(SINCE {_imap_date_since(2)})")
    uids = (r.lines[0] or b"").split()
    print(f"Seq nums in last 2 days: {len(uids)}")

    print("\n=== Scanning all recent emails ===")
    for uid in uids[:60]:
        r2 = await client.fetch(uid.decode(), "(RFC822)")
        raw = b""
        for line in r2.lines:
            if isinstance(line, bytearray) and len(line) > 0:
                raw += line
        parsed = parse_inbound(raw)
        match_id = None
        if parsed.from_addr and not NOREPLY_FROM_RX.search(parsed.from_addr):
            match_id = await _match_reply_by_sender(parsed.from_addr)
        flag = " <<< MATCH" if match_id else ""
        print(f"  seq {uid.decode()}: from={parsed.from_addr} kind={parsed.kind}{flag}")

    await client.logout()

asyncio.run(main())

import asyncio, ssl, email as emaillib, re
import aioimaplib
from outreach.db import SessionLocal
from outreach.models.channel import Channel
from outreach.utils.crypto import decrypt_json
from outreach.schemas.channels import IMAPConfig
from sqlalchemy import select

async def check():
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

    # Get folder list using raw IMAP command
    raw_resp = await client.protocol.execute(
        aioimaplib.Command("LIST", client.protocol.new_tag(), '""', '"*"')
    )
    print("=== Available folders ===")
    folders = []
    for line in raw_resp.lines:
        if isinstance(line, bytes):
            decoded = line.decode(errors="replace")
            # Extract folder name
            m = re.search(r'"([^"]+)"$', decoded)
            if not m:
                m = re.search(r'\S+$', decoded)
            if m:
                fname = m.group(1) if '"' in decoded else m.group(0)
                print(f"  {fname}")
                folders.append(fname)

    # Try All Mail specifically with quoted name
    for fname in folders:
        if "All" in fname or "all" in fname:
            print(f"\nTrying '{fname}'...")
            # Send SELECT with quoted mailbox
            resp = await client.protocol.execute(
                aioimaplib.Command("SELECT", client.protocol.new_tag(), f'"{fname}"')
            )
            print(f"  Select result: {resp.result}")
            if resp.result == "OK":
                r2 = await client.search("SINCE", "18-Jun-2026")
                uids = r2.lines[0].decode().split() if r2.lines and r2.lines[0] else []
                print(f"  Emails since Jun 18: {len(uids)}")
                # Look for coherentworks
                for uid in uids[-20:]:
                    r3 = await client.uid("fetch", uid, "(RFC822)")
                    raw = b""
                    for line in r3.lines:
                        if isinstance(line, (bytes, bytearray)) and len(line) > 0:
                            raw += line
                    for marker in [b"Delivered-To:", b"Return-Path:", b"From:"]:
                        idx = raw.find(marker)
                        if idx != -1:
                            raw = raw[idx:]
                            break
                    msg = emaillib.message_from_bytes(raw)
                    frm = str(msg["From"] or "-")
                    if "coherent" in frm.lower() or "steven" in frm.lower():
                        print(f"  *** FOUND! UID {uid}: {frm} | {msg['Subject']}")

    await client.logout()

asyncio.run(check())

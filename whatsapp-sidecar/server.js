/**
 * Coherent Outreach — WhatsApp sidecar (Baileys).
 *
 * Browserless WhatsApp Web automation. Replaces wa-automate (which fails to
 * initialize against current WhatsApp Web). Talks the WhatsApp multi-device
 * protocol directly over a WebSocket — no Chrome, no Puppeteer.
 *
 * Exposes a tiny REST surface the Python backend already targets:
 *   GET  /getConnectionState  -> { state, connected }
 *   GET  /qr                  -> { state, qr }   (qr is a PNG data URL while pairing)
 *   POST /sendText            -> { to, content } -> { ok, id }
 *   POST /logout              -> wipe session (forces a fresh QR)
 *
 * Inbound (non-group, not-from-me) messages are POSTed to BACKEND_INBOUND_URL.
 *
 * All endpoints require header `X-Api-Key: <WA_API_KEY>` (the backend proxy
 * injects it; the UI never talks to this port directly).
 *
 * Env:
 *   WA_PORT              (default 8085)
 *   WA_API_KEY           shared secret (required in production)
 *   WA_SESSION_PATH      auth-state dir (default ./data/wa-session)
 *   WA_BACKEND_INBOUND_URL  e.g. http://127.0.0.1:8000/api/whatsapp/inbound
 */
import { mkdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import makeWASocket, {
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  DisconnectReason,
} from "@whiskeysockets/baileys";
import express from "express";
import pino from "pino";
import QRCode from "qrcode";

const __dirname = dirname(fileURLToPath(import.meta.url));

const PORT = parseInt(process.env.WA_PORT || "8085", 10);
const API_KEY = process.env.WA_API_KEY || "";
const SESSION_PATH = resolve(
  process.env.WA_SESSION_PATH || `${__dirname}/data/wa-session`,
);
const INBOUND_URL = process.env.WA_BACKEND_INBOUND_URL || "";

const log = pino({ level: process.env.WA_LOG_LEVEL || "info" });

// Baileys occasionally rejects an internal promise (e.g. a keep-alive ping)
// with the raw WS close code when the socket drops abnormally (code 1006) —
// that rejection is never attached to a .catch(), so by default Node treats
// it as fatal and kills the whole sidecar even though connection.update's own
// "closed — reconnecting" handler below is already recovering. Don't let a
// stray rejection from a socket we're already replacing take the process down.
process.on("unhandledRejection", (reason) => {
  log.warn({ err: String(reason) }, "unhandled rejection — ignoring (reconnect already in flight)");
});
process.on("uncaughtException", (err) => {
  log.error({ err: String(err) }, "uncaught exception — ignoring (reconnect already in flight)");
});

// ── connection state machine ────────────────────────────────────────────────
// state: 'starting' | 'qr' | 'connected' | 'disconnected' | 'logged_out'
let state = "starting";
let currentQR = null; // PNG data URL while pairing, else null
let meId = null; // our own WhatsApp id once connected
let sock = null;
let starting = false;

function toJid(to) {
  // Accept "+1 (415) 555-2671", "14155552671", "14155552671@s.whatsapp.net"…
  const raw = String(to).trim();
  if (raw.includes("@")) return raw;
  const digits = raw.replace(/[^0-9]/g, "");
  return `${digits}@s.whatsapp.net`;
}

async function postInbound(payload) {
  if (!INBOUND_URL) return;
  try {
    await fetch(INBOUND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Api-Key": API_KEY },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    log.warn({ err: String(e) }, "inbound webhook POST failed");
  }
}

async function start() {
  if (starting) return;
  starting = true;
  try {
    mkdirSync(SESSION_PATH, { recursive: true });
    const { state: authState, saveCreds } =
      await useMultiFileAuthState(SESSION_PATH);
    const { version } = await fetchLatestBaileysVersion();

    sock = makeWASocket({
      version,
      auth: authState,
      logger: log.child({ mod: "baileys" }),
      printQRInTerminal: false,
      markOnlineOnConnect: false,
      browser: ["Coherent Outreach", "Chrome", "1.0.0"],
      defaultQueryTimeoutMs: undefined, // disable per-query timeout so fetchProps never times out
      connectTimeoutMs: 60_000,
    });

    sock.ev.on("creds.update", saveCreds);

    sock.ev.on("connection.update", async (update) => {
      const { connection, lastDisconnect, qr } = update;
      if (qr) {
        try {
          currentQR = await QRCode.toDataURL(qr, { margin: 1, width: 320 });
          state = "qr";
          log.info("QR refreshed — scan to link the WhatsApp number");
        } catch (e) {
          log.error({ err: String(e) }, "failed to render QR");
        }
      }
      if (connection === "open") {
        state = "connected";
        currentQR = null;
        meId = sock.user?.id || null;
        log.info({ meId }, "WhatsApp connected");
      }
      if (connection === "close") {
        const code = lastDisconnect?.error?.output?.statusCode;
        const loggedOut = code === DisconnectReason.loggedOut;
        currentQR = null;
        if (loggedOut) {
          log.warn("session logged out — wiping session and restarting for fresh QR");
          state = "starting";
          currentQR = null;
          meId = null;
          sock = null;
          try { rmSync(SESSION_PATH, { recursive: true, force: true }); } catch (_) {}
          mkdirSync(SESSION_PATH, { recursive: true });
          starting = false;
          start();
          return;
        } else {
          state = "disconnected";
          log.warn({ code }, "connection closed — reconnecting");
          starting = false;
          start();
          return;
        }
      }
    });

    sock.ev.on("messages.upsert", async ({ messages, type }) => {
      log.info({ type, count: messages.length }, "messages.upsert fired");
      const cutoff = type === "notify" ? 0 : Date.now() / 1000 - 600;
      for (const m of messages) {
        try {
          if (!m.message || m.key.fromMe) continue;
          if (Number(m.messageTimestamp) < cutoff) continue;
          const jid = m.key.remoteJid || "";
          if (jid.endsWith("@g.us") || jid === "status@broadcast") continue;
          const text =
            m.message.conversation ||
            m.message.extendedTextMessage?.text ||
            m.message.imageMessage?.caption ||
            m.message.videoMessage?.caption ||
            "";
          if (!text) { log.info({ jid, msgType: Object.keys(m.message || {}) }, "no text extracted"); continue; }
          // For LID JIDs (@lid), use senderPn for the real phone number
          const phoneJid = (jid.endsWith("@lid") && m.key.senderPn) ? m.key.senderPn : jid;
          const from = "+" + phoneJid.split("@")[0];
          log.info({ from, text: text.slice(0, 50) }, "forwarding inbound");
          await postInbound({
            id: m.key.id,
            from,
            text,
            timestamp: Number(m.messageTimestamp) || null,
            push_name: m.pushName || null,
          });
        } catch (e) {
          log.warn({ err: String(e) }, "failed to process inbound message");
        }
      }
    });
  } finally {
    starting = false;
  }
}

// ── REST API ────────────────────────────────────────────────────────────────
const app = express();
app.use(express.json({ limit: "1mb" }));

// Shared-secret guard. When WA_API_KEY is unset (local dev), allow all.
app.use((req, res, next) => {
  if (!API_KEY) return next();
  if (req.get("X-Api-Key") === API_KEY) return next();
  return res.status(401).json({ ok: false, error: "bad api key" });
});

app.get("/getConnectionState", (_req, res) => {
  res.json({ state, connected: state === "connected", me: meId });
});

app.get("/qr", (_req, res) => {
  res.json({ state, qr: state === "connected" ? null : currentQR });
});

app.post("/sendText", async (req, res) => {
  const { to, content } = req.body || {};
  if (!to || !content) {
    return res.status(400).json({ ok: false, error: "to and content required" });
  }
  if (state !== "connected" || !sock) {
    return res.status(503).json({ ok: false, error: `not connected (${state})` });
  }
  try {
    const sent = await sock.sendMessage(toJid(to), { text: String(content) });
    res.json({ ok: true, id: sent?.key?.id || null });
  } catch (e) {
    log.warn({ err: String(e) }, "sendText failed");
    res.status(502).json({ ok: false, error: String(e?.message || e) });
  }
});

// ── PAIRING CODE FEATURE — remove this block to disable phone-number linking ──
app.post("/requestPairingCode", async (req, res) => {
  const { phone } = req.body || {};
  if (!phone) return res.status(400).json({ ok: false, error: "phone required" });
  if (!sock) return res.status(503).json({ ok: false, error: "sidecar not ready" });
  if (state === "connected") return res.status(400).json({ ok: false, error: "already connected" });
  try {
    const digits = String(phone).replace(/[^0-9]/g, "");
    const code = await sock.requestPairingCode(digits);
    log.info({ digits }, "pairing code issued");
    res.json({ ok: true, code });
  } catch (e) {
    log.warn({ err: String(e) }, "requestPairingCode failed");
    res.status(502).json({ ok: false, error: String(e?.message || e) });
  }
});
// ── END PAIRING CODE FEATURE ─────────────────────────────────────────────────

app.post("/logout", async (_req, res) => {
  try {
    if (sock) await sock.logout().catch(() => {});
  } catch (e) {
    log.warn({ err: String(e) }, "logout error (ignored)");
  }
  try {
    rmSync(SESSION_PATH, { recursive: true, force: true });
  } catch (e) {
    log.warn({ err: String(e) }, "session wipe error (ignored)");
  }
  state = "starting";
  currentQR = null;
  meId = null;
  sock = null;
  starting = false;
  start();
  res.json({ ok: true });
});

app.listen(PORT, "127.0.0.1", () => {
  log.info(`WhatsApp sidecar listening on http://127.0.0.1:${PORT}`);
  start();
});

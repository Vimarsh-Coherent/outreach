import { api } from "./client";

export type SecurityMode = "ssl_tls" | "starttls" | "none";

export interface SMTPConfig {
  host: string;
  port: number;
  security: SecurityMode;
  username: string;
  password: string;
  from_email: string;
  from_name?: string | null;
  timeout_seconds?: number;
}

export interface IMAPConfig {
  host: string;
  port: number;
  security: SecurityMode;
  username: string;
  password: string;
  mailbox?: string;
  timeout_seconds?: number;
}

export interface EmailPreset {
  key: string;
  label: string;
  smtp_host: string;
  smtp_port: number;
  smtp_security: SecurityMode;
  imap_host: string;
  imap_port: number;
  imap_security: SecurityMode;
  notes?: string | null;
}

export interface TestStepResult {
  ok: boolean;
  detail: string;
  latency_ms?: number | null;
}

export interface TestEmailChannelResponse {
  smtp_connect: TestStepResult;
  smtp_auth: TestStepResult;
  smtp_probe_send?: TestStepResult | null;
  imap_connect?: TestStepResult | null;
  imap_auth?: TestStepResult | null;
}

export interface ChannelOut {
  id: number;
  channel_type: string;
  display_label: string;
  status: string;
  daily_cap: number;
  sent_today: number;
  created_at: string;
  updated_at: string;
  smtp_host?: string | null;
  smtp_port?: number | null;
  smtp_from_email?: string | null;
  imap_host?: string | null;
  imap_port?: number | null;
}

export async function listChannels(): Promise<ChannelOut[]> {
  const r = await api.get<ChannelOut[]>("/channels");
  return r.data;
}

export async function getEmailPresets(): Promise<EmailPreset[]> {
  const r = await api.get<EmailPreset[]>("/channels/email/presets");
  return r.data;
}

export async function testEmailChannel(body: {
  smtp: SMTPConfig;
  imap?: IMAPConfig | null;
  send_probe_to?: string | null;
}): Promise<TestEmailChannelResponse> {
  const r = await api.post<TestEmailChannelResponse>("/channels/email/test", body);
  return r.data;
}

export async function createEmailChannel(body: {
  display_label: string;
  smtp: SMTPConfig;
  imap?: IMAPConfig | null;
  daily_cap: number;
}): Promise<ChannelOut> {
  const r = await api.post<ChannelOut>("/channels/email", body);
  return r.data;
}

export async function deleteChannel(id: number): Promise<void> {
  await api.delete(`/channels/${id}`);
}

// ── WhatsApp (Baileys sidecar) ──────────────────────────────────────────────

export interface WhatsAppStatus {
  state: string; // starting | qr | connected | disconnected | logged_out | unavailable
  connected: boolean;
  me?: string | null;
  channel_id?: number | null;
}

export interface WhatsAppQr {
  state: string;
  qr?: string | null; // PNG data URL while pairing
}

export async function getWhatsAppStatus(): Promise<WhatsAppStatus> {
  const r = await api.get<WhatsAppStatus>("/channels/whatsapp/status");
  return r.data;
}

export async function getWhatsAppQr(): Promise<WhatsAppQr> {
  const r = await api.get<WhatsAppQr>("/channels/whatsapp/qr");
  return r.data;
}

export async function createWhatsAppChannel(body: {
  display_label: string;
  daily_cap: number;
}): Promise<ChannelOut> {
  const r = await api.post<ChannelOut>("/channels/whatsapp", body);
  return r.data;
}

export async function logoutWhatsApp(): Promise<{ ok: boolean }> {
  const r = await api.post<{ ok: boolean }>("/channels/whatsapp/logout", {});
  return r.data;
}

// PAIRING CODE FEATURE — remove this function to disable phone-number linking
export async function requestWaPairingCode(phone: string): Promise<{ ok: boolean; code?: string; error?: string }> {
  const r = await api.post("/whatsapp/pairing-code", { phone });
  return r.data;
}
// END PAIRING CODE FEATURE

// ── LinkedIn (Chrome extension bridge) ──────────────────────────────────────

export interface LinkedInChannelCreated {
  id: number;
  display_label: string;
  daily_cap: number;
  raw_token: string; // shown ONCE — paste into the extension popup, then it's gone
}

export async function createLinkedInChannel(body: {
  display_label: string;
  daily_cap: number;
}): Promise<LinkedInChannelCreated> {
  const r = await api.post<LinkedInChannelCreated>("/channels/linkedin", body);
  return r.data;
}

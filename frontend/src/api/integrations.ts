import { api } from "./client";

export interface WebhookEndpoint {
  id: number;
  url: string;
  event_types: string[];
  description: string | null;
  active: boolean;
  secret: string;
  created_at: string;
}

export interface ApiKeyOut {
  id: number;
  label: string;
  prefix: string;
  last_used_at: string | null;
  created_at: string;
}

export async function getWebhookEventTypes(): Promise<string[]> {
  return (await api.get<string[]>("/webhooks/event-types")).data;
}
export async function listWebhooks(): Promise<WebhookEndpoint[]> {
  return (await api.get<WebhookEndpoint[]>("/webhooks")).data;
}
export async function createWebhook(body: { url: string; event_types: string[]; description?: string }): Promise<WebhookEndpoint> {
  return (await api.post<WebhookEndpoint>("/webhooks", body)).data;
}
export async function deleteWebhook(id: number): Promise<void> {
  await api.delete(`/webhooks/${id}`);
}
export async function testWebhook(id: number): Promise<{ ok: boolean; status?: number; error?: string }> {
  return (await api.post(`/webhooks/${id}/test`)).data;
}

export async function listApiKeys(): Promise<ApiKeyOut[]> {
  return (await api.get<ApiKeyOut[]>("/keys")).data;
}
export async function createApiKey(label: string): Promise<{ id: number; label: string; prefix: string; raw_key: string }> {
  return (await api.post("/keys", { label })).data;
}
export async function deleteApiKey(id: number): Promise<void> {
  await api.delete(`/keys/${id}`);
}

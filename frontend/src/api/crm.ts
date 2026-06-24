import { api } from "./client";

export interface HubspotStatus {
  configured: boolean;
  connected: boolean;
  portal_name: string | null;
  last_synced_at: string | null;
}

export async function getHubspotStatus(): Promise<HubspotStatus> {
  return (await api.get<HubspotStatus>("/crm/hubspot/status")).data;
}
export async function hubspotConnectUrl(): Promise<string> {
  return (await api.get<{ authorize_url: string }>("/crm/hubspot/connect")).data.authorize_url;
}
export async function hubspotDisconnect(): Promise<void> {
  await api.post("/crm/hubspot/disconnect");
}
export async function hubspotSyncContacts(): Promise<{ pulled: number; inserted: number; updated: number }> {
  return (await api.post("/crm/hubspot/sync-contacts")).data;
}

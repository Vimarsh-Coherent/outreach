import { api } from "./client";

export interface UserProfile {
  id: number;
  email: string;
  display_name: string;
  meeting_link: string | null;
}

export async function getProfile(): Promise<UserProfile> {
  return (await api.get<UserProfile>("/users/profile")).data;
}

export async function updateProfile(dto: { meeting_link: string | null }): Promise<UserProfile> {
  return (await api.patch<UserProfile>("/users/profile", dto)).data;
}

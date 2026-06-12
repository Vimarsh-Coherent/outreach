import axios from "axios";

export const api = axios.create({
  baseURL: "/api",
  withCredentials: true,
});

export async function getHealth() {
  const res = await axios.get("/health");
  return res.data as { status: string; db: boolean; db_error: string | null };
}

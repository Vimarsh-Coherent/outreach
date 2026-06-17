import { api } from "./client";

export type RagDocumentStatus = "pending" | "indexed" | "failed";

export interface SequenceRagDocumentOut {
  id: number;
  filename: string;
  mime_type: string;
  file_size: number;
  status: RagDocumentStatus;
  chunk_count: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export async function listRagDocuments() {
  return (await api.get<SequenceRagDocumentOut[]>("/sequences/rag/documents")).data;
}

export async function uploadRagDocuments(files: File[]) {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  return (await api.post<{ documents: SequenceRagDocumentOut[] }>("/sequences/rag/documents", fd, {
    headers: { "Content-Type": "multipart/form-data" },
  })).data;
}

export async function deleteRagDocument(docId: number) {
  await api.delete(`/sequences/rag/documents/${docId}`);
}

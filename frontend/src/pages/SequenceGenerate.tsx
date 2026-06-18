import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import axios from "axios";

import { generateSequenceFromPrompt } from "../api/sequences";
import {
  SequenceRagDocumentOut,
  deleteRagDocument,
  listRagDocuments,
  uploadRagDocuments,
} from "../api/sequenceRag";

const ACCEPT = ".pdf,.docx,.txt,.md";

function extractError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      const violations = (detail as { violations?: string[] }).violations;
      if (violations?.length) {
        return `${(detail as { message: string }).message}: ${violations.join("; ")}`;
      }
      return String((detail as { message: string }).message);
    }
    return err.message;
  }
  return "Something went wrong — try again.";
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function SequenceGenerate() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [prompt, setPrompt] = useState("");
  const [tz, setTz] = useState("Asia/Kolkata");
  const [error, setError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const { data: documents, refetch: refetchDocs } = useQuery({
    queryKey: ["sequence-rag-documents"],
    queryFn: listRagDocuments,
  });

  const uploadMut = useMutation({
    mutationFn: async (files: File[]) => uploadRagDocuments(files),
    onSuccess: (data) => {
      refetchDocs();
      setSelectedIds(prev => {
        const next = new Set(prev);
        for (const d of data.documents) {
          if (d.status === "indexed") next.add(d.id);
        }
        return next;
      });
      if (fileRef.current) fileRef.current.value = "";
    },
    onError: (err) => setError(extractError(err)),
  });

  const deleteMut = useMutation({
    mutationFn: async (id: number) => deleteRagDocument(id),
    onSuccess: (_, id) => {
      refetchDocs();
      setSelectedIds(prev => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    },
    onError: (err) => setError(extractError(err)),
  });

  const generateMut = useMutation({
    mutationFn: async () => {
      const indexedIds = documents
        ?.filter((d: SequenceRagDocumentOut) => d.status === "indexed" && selectedIds.has(d.id))
        .map((d: SequenceRagDocumentOut) => d.id) ?? [];
      return generateSequenceFromPrompt({
        prompt: prompt.trim(),
        timezone: tz,
        document_ids: indexedIds.length ? indexedIds : undefined,
      });
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["sequences"] });
      // Land in the editor (step list + flow + document-grounding chips) so the
      // user can review/edit the freshly generated, grounded sequence.
      navigate(`/sequences/${data.sequence.id}`);
    },
    onError: (err) => setError(extractError(err)),
  });

  const canSubmit = prompt.trim().length >= 10 && !generateMut.isPending && !uploadMut.isPending;

  const toggleDoc = (id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const onFilesPicked = (files: FileList | null) => {
    if (!files?.length) return;
    uploadMut.mutate(Array.from(files));
  };

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <Link to="/sequences" className="text-sm text-sky-700 hover:underline">&larr; Back to sequences</Link>
        <h2 className="text-2xl font-semibold mt-2">Generate sequence with AI</h2>
        <p className="text-sm text-slate-500 mt-1">
          Upload pitch documents (optional), describe the sequence you want, and AI will draft a cadence using your docs as context.
        </p>
      </div>

      {error && (
        <div className="rounded border border-rose-200 bg-rose-50 text-rose-800 text-sm p-3">
          {error}
        </div>
      )}

      <section className="rounded border bg-white p-4 space-y-3">
        <h3 className="text-sm font-medium text-slate-700">Documents (optional)</h3>
        <p className="text-xs text-slate-500">PDF, DOCX, TXT, or MD — max 20 MB each. Indexed docs are used as RAG context when generating.</p>
        <div className="flex gap-2 items-center">
          <input
            ref={fileRef}
            type="file"
            accept={ACCEPT}
            multiple
            className="text-sm"
            onChange={(e) => onFilesPicked(e.target.files)}
          />
          {uploadMut.isPending && <span className="text-xs text-slate-500">Uploading & indexing…</span>}
        </div>
        {documents?.length ? (
          <ul className="divide-y text-sm">
            {documents.map((d: SequenceRagDocumentOut) => (
              <li key={d.id} className="py-2 flex items-center gap-3">
                {d.status === "indexed" ? (
                  <input
                    type="checkbox"
                    checked={selectedIds.has(d.id)}
                    onChange={() => toggleDoc(d.id)}
                    className="rounded"
                  />
                ) : (
                  <span className="w-4" />
                )}
                <div className="flex-1 min-w-0">
                  <div className="font-medium truncate">{d.filename}</div>
                  <div className="text-xs text-slate-500">
                    {formatSize(d.file_size)}
                    {d.status === "indexed" && ` · ${d.chunk_count} chunks`}
                    {d.status === "failed" && d.error_message && ` · ${d.error_message}`}
                  </div>
                </div>
                <span className={`text-xs px-2 py-0.5 rounded ${
                  d.status === "indexed" ? "bg-emerald-100 text-emerald-800"
                  : d.status === "failed" ? "bg-rose-100 text-rose-800"
                  : "bg-slate-100 text-slate-600"
                }`}>{d.status}</span>
                <button
                  type="button"
                  onClick={() => deleteMut.mutate(d.id)}
                  className="text-xs text-rose-700 hover:underline"
                  disabled={deleteMut.isPending}
                >
                  delete
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-slate-500">No documents uploaded yet.</p>
        )}
      </section>

      <section className="rounded border bg-white p-4 space-y-4">
        <label className="block text-sm">
          <span className="text-slate-600 mb-1 block">Prompt</span>
          <textarea
            value={prompt}
            onChange={(e) => { setPrompt(e.target.value); setError(null); }}
            rows={8}
            className="w-full border rounded px-3 py-2 text-sm"
            placeholder="Example: 3-email SaaS outreach to CTOs at mid-size companies. Friendly tone. Follow up every 3 days."
          />
          <span className="text-xs text-slate-500 mt-1 block">{prompt.trim().length}/4000 (min 10)</span>
        </label>

        <label className="block text-sm">
          <span className="text-slate-600 mb-1 block">Timezone</span>
          <input
            value={tz}
            onChange={(e) => setTz(e.target.value)}
            className="w-full border rounded px-2 py-1.5 font-mono text-sm"
            placeholder="Asia/Kolkata"
          />
        </label>

        <div className="flex gap-3">
          <button
            disabled={!canSubmit}
            onClick={() => generateMut.mutate()}
            className="border rounded px-4 py-1.5 bg-sky-600 text-white hover:bg-sky-700 disabled:bg-slate-300"
          >
            {generateMut.isPending ? "Generating sequence…" : "Generate sequence"}
          </button>
          <Link to="/sequences" className="border rounded px-4 py-1.5 text-sm hover:bg-slate-50 inline-flex items-center">
            Cancel
          </Link>
        </div>
      </section>

      <p className="text-xs text-slate-500">
        Uses template tokens like <code className="bg-slate-100 px-1">{"{{first_name}}"}</code> and{" "}
        <code className="bg-slate-100 px-1">{"{{company}}"}</code>. Requires Qdrant running (<code className="bg-slate-100 px-1">docker compose up qdrant</code>) for document indexing.
      </p>
    </div>
  );
}

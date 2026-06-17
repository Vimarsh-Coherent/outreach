import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  LeadField,
  UploadCommitResponse,
  UploadPreviewResponse,
  commitUpload,
  createLead,
  deleteLead,
  formatAxiosError,
  listLeads,
  previewUpload,
} from "../api/leads";

const LEAD_FIELDS: { key: LeadField; label: string; required?: boolean }[] = [
  { key: "email", label: "Email" },
  { key: "first_name", label: "First name" },
  { key: "last_name", label: "Last name" },
  { key: "phone", label: "Phone" },
  { key: "linkedin_url", label: "LinkedIn URL" },
  { key: "company", label: "Company" },
  { key: "title", label: "Title" },
];

const PAGE_SIZE = 50;

function blankMapping(): Record<LeadField, string | null> {
  return Object.fromEntries(LEAD_FIELDS.map(f => [f.key, null])) as Record<LeadField, string | null>;
}

export default function Leads() {
  const qc = useQueryClient();

  const [page, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const { data: leadsData } = useQuery({
    queryKey: ["leads", { search, page }],
    queryFn: () => listLeads({ search: search || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
  });

  const [preview, setPreview] = useState<UploadPreviewResponse | null>(null);
  const [mapping, setMapping] = useState<Record<LeadField, string | null>>(blankMapping());
  const [commitResult, setCommitResult] = useState<UploadCommitResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Manual single-lead add
  const [manualOpen, setManualOpen] = useState(false);
  const [manual, setManual] = useState<Record<LeadField, string>>({
    email: "", first_name: "", last_name: "", phone: "",
    linkedin_url: "", company: "", title: "",
  });
  const [manualStatus, setManualStatus] = useState<string | null>(null);

  const manualMut = useMutation({
    mutationFn: async () => {
      setManualStatus(null);
      const payload = Object.fromEntries(
        Object.entries(manual).filter(([_, v]) => v.trim() !== "")
      );
      return createLead(payload);
    },
    onSuccess: lead => {
      setManualStatus(`Added: ${lead.first_name ?? ""} ${lead.last_name ?? ""}`.trim() + ` (#${lead.id})`);
      setManual({ email: "", first_name: "", last_name: "", phone: "", linkedin_url: "", company: "", title: "" });
      qc.invalidateQueries({ queryKey: ["leads"] });
    },
    onError: e => setManualStatus("Error: " + formatAxiosError(e)),
  });

  const previewMut = useMutation({
    mutationFn: async (file: File) => previewUpload(file),
    onSuccess: r => {
      setPreview(r);
      setMapping(r.suggested_mapping);
      setCommitResult(null);
      setError(null);
    },
    onError: e => { setError(formatAxiosError(e)); setPreview(null); },
  });

  const commitMut = useMutation({
    mutationFn: async () => {
      if (!preview) throw new Error("no preview");
      return commitUpload({ token: preview.token, mapping });
    },
    onSuccess: r => {
      setCommitResult(r);
      setPreview(null);
      qc.invalidateQueries({ queryKey: ["leads"] });
    },
    onError: e => setError(formatAxiosError(e)),
  });

  const deleteMut = useMutation({
    mutationFn: async (id: number) => deleteLead(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["leads"] }),
  });

  const totalPages = leadsData ? Math.ceil(leadsData.total / PAGE_SIZE) : 0;
  const hasIdentityMapped = !!(mapping.email || mapping.phone || mapping.linkedin_url);

  return (
    <div className="space-y-8 max-w-6xl">
      <div>
        <h2 className="text-2xl font-semibold">Leads</h2>
        <p className="text-sm text-slate-500 mt-1">
          Upload CSV / TSV / Excel. Columns are auto-mapped; you can override them before committing. Dedupe is by canonical identity (email → phone → LinkedIn slug).
        </p>
      </div>

      <section className="rounded border bg-white p-6 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-slate-800">Add a single lead manually</h3>
          <button onClick={() => setManualOpen(o => !o)} className="text-sm text-sky-700 hover:underline">
            {manualOpen ? "Hide" : "+ Add manually"}
          </button>
        </div>
        {manualOpen && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
              {LEAD_FIELDS.map(f => (
                <label key={f.key}>
                  <span className="block text-slate-600 mb-1">{f.label}</span>
                  <input
                    value={manual[f.key]}
                    onChange={e => setManual({ ...manual, [f.key]: e.target.value })}
                    placeholder={f.key === "email" ? "alex@acme.io"
                      : f.key === "phone" ? "+1 415 555 1234"
                      : f.key === "linkedin_url" ? "https://www.linkedin.com/in/alex"
                      : ""}
                    className="w-full border rounded px-2 py-1.5 font-mono text-xs"
                  />
                </label>
              ))}
            </div>
            <div className="text-xs text-slate-500">
              At least one of <strong>email</strong>, <strong>phone</strong>, or <strong>LinkedIn URL</strong> is required.
              Phones are normalised to E.164; LinkedIn URLs to <code>https://www.linkedin.com/in/&lt;slug&gt;</code>.
              If a lead with the same canonical identifier already exists, this updates it instead of duplicating.
            </div>
            <button
              disabled={manualMut.isPending || (!manual.email.trim() && !manual.phone.trim() && !manual.linkedin_url.trim())}
              onClick={() => manualMut.mutate()}
              className="border rounded px-4 py-1.5 bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-slate-300"
            >
              {manualMut.isPending ? "Saving..." : "Save lead"}
            </button>
            {manualStatus && (
              <div className={`text-sm rounded p-2 ${manualStatus.startsWith("Error") ? "bg-rose-50 text-rose-800" : "bg-emerald-50 text-emerald-800"}`}>
                {manualStatus}
              </div>
            )}
          </>
        )}
      </section>

      <section className="rounded border bg-white p-6 space-y-4">
        <h3 className="font-semibold text-slate-800">Or upload a CSV / Excel file</h3>
        <input
          type="file"
          accept=".csv,.tsv,.xlsx,.xls"
          disabled={previewMut.isPending}
          onChange={e => {
            const f = e.target.files?.[0];
            if (f) previewMut.mutate(f);
          }}
          className="block text-sm"
        />
        {previewMut.isPending && <div className="text-sm text-slate-500">Parsing...</div>}
        {error && <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded p-2">{error}</div>}

        {preview && (
          <div className="space-y-4">
            <div className="text-sm text-slate-700">
              <strong>{preview.row_count}</strong> rows · <strong>{preview.columns.length}</strong> columns detected
            </div>

            <div>
              <h4 className="font-medium text-slate-700 mb-2">Map columns</h4>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
                {LEAD_FIELDS.map(f => (
                  <label key={f.key}>
                    <span className="block text-slate-600 mb-1">{f.label}</span>
                    <select
                      value={mapping[f.key] ?? ""}
                      onChange={e => setMapping({ ...mapping, [f.key]: e.target.value || null })}
                      className="w-full border rounded px-2 py-1.5"
                    >
                      <option value="">— ignore —</option>
                      {preview.columns.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </label>
                ))}
              </div>
              {!hasIdentityMapped && (
                <div className="mt-3 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded p-2">
                  Map at least one of <strong>email</strong>, <strong>phone</strong>, or <strong>linkedin_url</strong>. Rows without any of these are skipped.
                </div>
              )}
            </div>

            <div>
              <h4 className="font-medium text-slate-700 mb-2">Preview (first 10 rows)</h4>
              <div className="border rounded overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="bg-slate-50 text-left text-slate-600">
                    <tr>{preview.columns.map(c => <th key={c} className="px-2 py-1.5 font-medium">{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    {preview.sample_rows.map((row, i) => (
                      <tr key={i} className="border-t">
                        {preview.columns.map(c => <td key={c} className="px-2 py-1 font-mono">{String(row[c] ?? "")}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <button
                onClick={() => commitMut.mutate()}
                disabled={!hasIdentityMapped || commitMut.isPending}
                className="border rounded px-4 py-1.5 bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-slate-300"
              >
                {commitMut.isPending ? "Importing..." : `Import ${preview.row_count} rows`}
              </button>
              <button
                onClick={() => { setPreview(null); setMapping(blankMapping()); }}
                className="text-sm text-slate-600 hover:underline"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {commitResult && (
          <div className="rounded border border-emerald-200 bg-emerald-50 p-3 text-sm">
            <div className="font-semibold text-emerald-900 mb-1">Import complete</div>
            <div className="grid grid-cols-5 gap-3 text-emerald-900">
              <div><div className="text-2xl font-bold">{commitResult.inserted}</div><div className="text-xs">inserted</div></div>
              <div><div className="text-2xl font-bold">{commitResult.updated}</div><div className="text-xs">updated</div></div>
              <div><div className="text-2xl font-bold">{commitResult.merged_within_upload}</div><div className="text-xs">merged in file</div></div>
              <div><div className="text-2xl font-bold">{commitResult.skipped_no_identity}</div><div className="text-xs">skipped — no identity</div></div>
              <div><div className="text-2xl font-bold">{commitResult.skipped_invalid}</div><div className="text-xs">skipped — invalid</div></div>
            </div>
          </div>
        )}
      </section>

      <section className="rounded border bg-white p-6">
        <div className="flex items-end gap-4 mb-4">
          <h3 className="font-semibold text-slate-800 flex-1">Your leads {leadsData && <span className="text-slate-500 font-normal text-sm">({leadsData.total})</span>}</h3>
          <input
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") { setSearch(searchInput); setPage(0); } }}
            placeholder="search email, name, company..."
            className="border rounded px-3 py-1.5 text-sm w-72"
          />
          <button onClick={() => { setSearch(searchInput); setPage(0); }} className="border rounded px-3 py-1.5 text-sm">Search</button>
        </div>
        {!leadsData?.items.length ? (
          <p className="text-sm text-slate-500">No leads yet.</p>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-slate-500">
                  <tr>
                    <th className="py-1 pr-3">Name</th>
                    <th className="py-1 pr-3">Email</th>
                    <th className="py-1 pr-3">Company / Title</th>
                    <th className="py-1 pr-3">Phone</th>
                    <th className="py-1 pr-3">LinkedIn</th>
                    <th className="py-1 pr-3">Source</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {leadsData.items.map(l => (
                    <tr key={l.id} className="border-t">
                      <td className="py-2 pr-3">{[l.first_name, l.last_name].filter(Boolean).join(" ") || "—"}</td>
                      <td className="py-2 pr-3 font-mono text-xs">{l.email || "—"}</td>
                      <td className="py-2 pr-3">{l.company || "—"}{l.title ? <div className="text-xs text-slate-500">{l.title}</div> : null}</td>
                      <td className="py-2 pr-3 font-mono text-xs">{l.phone || "—"}</td>
                      <td className="py-2 pr-3">{l.linkedin_url ? <a className="text-sky-600 hover:underline text-xs" href={l.linkedin_url} target="_blank" rel="noreferrer">profile</a> : "—"}</td>
                      <td className="py-2 pr-3 text-xs text-slate-500">{l.source}</td>
                      <td className="py-2">
                        <button onClick={() => { if (confirm("Delete this lead?")) deleteMut.mutate(l.id); }} className="text-rose-600 hover:underline text-xs">delete</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between mt-4 text-sm">
              <div className="text-slate-500">Page {page + 1} of {totalPages}</div>
              <div className="flex gap-2">
                <button disabled={page === 0} onClick={() => setPage(p => Math.max(0, p - 1))} className="border rounded px-3 py-1 disabled:text-slate-300">Prev</button>
                <button disabled={page + 1 >= totalPages} onClick={() => setPage(p => p + 1)} className="border rounded px-3 py-1 disabled:text-slate-300">Next</button>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

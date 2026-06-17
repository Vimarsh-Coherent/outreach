import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useState } from "react";

import { Link, useParams } from "react-router-dom";



import { listLeads } from "../api/leads";

import SequenceFlowBuilder from "../components/SequenceFlowBuilder";

import SequenceDetailedSteps from "../components/SequenceDetailedSteps";

import StepEditModal from "../components/StepEditModal";

import {

  StepChannel,

  StepCreate,

  StepOut,

  addStep,

  enrolLeads,

  getSequence,

} from "../api/sequences";



const CHANNEL_BODY_CAP: Record<StepChannel, number> = {

  email: 16000,

  linkedin_dm: 8000,

  linkedin_connect: 300,

  call: 4000,

  sms: 1600,

  whatsapp: 4000,

};



function DAYS_MASK_LABEL(mask: number): string {

  const names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  return names.filter((_, i) => mask & (1 << i)).join("·");

}



export default function SequenceEditor() {

  const { id } = useParams();

  const sequenceId = Number(id);

  const qc = useQueryClient();



  const { data: seq } = useQuery({

    queryKey: ["sequence", sequenceId],

    queryFn: () => getSequence(sequenceId),

    enabled: !Number.isNaN(sequenceId),

  });



  const [selectedStep, setSelectedStep] = useState<StepOut | null>(null);



  // Add-step form

  const [channel, setChannel] = useState<StepChannel>("email");

  const [subject, setSubject] = useState("");

  const [body, setBody] = useState("");

  const [delayDays, setDelayDays] = useState(0);

  const [delayHours, setDelayHours] = useState(0);



  const addMut = useMutation({

    mutationFn: async () => {

      const payload: StepCreate = {

        channel, body, delay_days: delayDays, delay_hours: delayHours,

        subject: channel === "email" ? subject : null,

      };

      return addStep(sequenceId, payload);

    },

    onSuccess: () => {

      qc.invalidateQueries({ queryKey: ["sequence", sequenceId] });

      setSubject(""); setBody(""); setDelayDays(0); setDelayHours(0);

    },

  });



  // Enrol panel

  const [enrolOpen, setEnrolOpen] = useState(false);

  const [selectedLeads, setSelectedLeads] = useState<number[]>([]);

  const { data: leads } = useQuery({

    queryKey: ["leads", { search: "", page: 0 }],

    queryFn: () => listLeads({ limit: 100, offset: 0 }),

    enabled: enrolOpen,

  });

  const enrolMut = useMutation({

    mutationFn: async () => enrolLeads(sequenceId, selectedLeads),

    onSuccess: r => {

      qc.invalidateQueries({ queryKey: ["sequence", sequenceId] });

      alert(`Enrolled: ${r.enrolled}\nDeduped: ${r.deduped}\nSkipped (no identity): ${r.skipped_no_identity}`);

      setSelectedLeads([]);

      setEnrolOpen(false);

    },

  });



  if (!seq) return <div className="text-sm text-slate-500">Loading...</div>;



  const bodyCap = CHANNEL_BODY_CAP[channel];

  const bodyOver = body.length > bodyCap;



  // Keep edit modal in sync after reorder / save

  const editStep =

    selectedStep != null

      ? seq.steps.find(s => s.id === selectedStep.id) ?? selectedStep

      : null;



  return (

    <div className="space-y-6 max-w-7xl">

      <div className="text-sm">

        <Link to="/sequences" className="text-sky-600 hover:underline">← back to sequences</Link>

      </div>

      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">

        <div>

          <h2 className="text-2xl font-semibold">{seq.name}</h2>

          <div className="text-sm text-slate-500 mt-1">

            <span className="inline-block px-2 py-0.5 rounded text-xs bg-slate-100 mr-2">{seq.status}</span>

            <span className="font-mono">{seq.timezone}</span> · {seq.send_window_start.slice(0,5)}–{seq.send_window_end.slice(0,5)} · {DAYS_MASK_LABEL(seq.send_days_mask)}

            {seq.ai_knowledge_id && (

              <span className="ml-2 text-xs px-2 py-0.5 bg-violet-100 text-violet-700 rounded">AI generated</span>

            )}

            {seq.ai_followups_enabled && (

              <span className="ml-2 text-xs px-2 py-0.5 bg-violet-100 text-violet-700 rounded">AI follow-ups</span>

            )}

          </div>

          {seq.description && <p className="text-sm text-slate-700 mt-2 max-w-2xl">{seq.description}</p>}

        </div>

        <button onClick={() => setEnrolOpen(o => !o)} disabled={seq.steps.length === 0} className="border rounded px-3 py-1.5 bg-sky-600 text-white hover:bg-sky-700 disabled:bg-slate-300 shrink-0">

          {enrolOpen ? "Close" : "Enrol leads"}

        </button>

      </div>



      <SequenceFlowBuilder

        sequenceId={sequenceId}

        steps={seq.steps}

        selectedStepId={selectedStep?.id ?? null}

        onSelectStep={setSelectedStep}

      />



      <SequenceDetailedSteps

        steps={seq.steps}

        selectedStepId={selectedStep?.id ?? null}

        onSelectStep={setSelectedStep}

      />



      <section className="rounded border bg-white p-4 space-y-4">

        <h3 className="font-semibold text-slate-800">Add step</h3>

        <div className="grid grid-cols-12 gap-3 text-sm">

          <label className="col-span-4"><span className="block text-slate-600 mb-1">Channel</span>

            <select value={channel} onChange={e => setChannel(e.target.value as StepChannel)} className="w-full border rounded px-2 py-1.5">

              <option value="email">Email</option>

              <option value="linkedin_dm">LinkedIn DM</option>

              <option value="linkedin_connect">LinkedIn connect (note)</option>

              <option value="call">Call task</option>

              <option value="sms">SMS</option>

              <option value="whatsapp">WhatsApp</option>

            </select>

          </label>

          <label className="col-span-4"><span className="block text-slate-600 mb-1">Delay days</span>

            <input type="number" min={0} max={365} value={delayDays} onChange={e => setDelayDays(Number(e.target.value))} className="w-full border rounded px-2 py-1.5" />

          </label>

          <label className="col-span-4"><span className="block text-slate-600 mb-1">Delay hours</span>

            <input type="number" min={0} max={23} value={delayHours} onChange={e => setDelayHours(Number(e.target.value))} className="w-full border rounded px-2 py-1.5" />

          </label>

          {channel === "email" && (

            <label className="col-span-12"><span className="block text-slate-600 mb-1">Subject (max 250)</span>

              <input value={subject} onChange={e => setSubject(e.target.value)} maxLength={250} className="w-full border rounded px-2 py-1.5" placeholder="Quick follow-up on {{company}}" />

            </label>

          )}

          <label className="col-span-12">

            <span className="block text-slate-600 mb-1">

              Body

              <span className={`ml-2 text-xs ${bodyOver ? "text-rose-600" : "text-slate-400"}`}>{body.length}/{bodyCap}</span>

            </span>

            <textarea value={body} onChange={e => setBody(e.target.value)} rows={6} className={`w-full border rounded px-2 py-1.5 font-mono text-xs ${bodyOver ? "border-rose-400" : ""}`} placeholder="Hi {{first_name}}, ..." />

            <div className="text-xs text-slate-500 mt-1">Tokens: <code className="bg-slate-100 px-1">{"{{first_name}}"}</code> <code className="bg-slate-100 px-1">{"{{last_name}}"}</code> <code className="bg-slate-100 px-1">{"{{company}}"}</code> <code className="bg-slate-100 px-1">{"{{title}}"}</code> <code className="bg-slate-100 px-1">{"{{email}}"}</code></div>

          </label>

        </div>

        <button

          disabled={addMut.isPending || !body.trim() || bodyOver || (channel === "email" && !subject.trim())}

          onClick={() => addMut.mutate()}

          className="border rounded px-4 py-1.5 bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-slate-300"

        >

          {addMut.isPending ? "Adding..." : "Add step"}

        </button>

      </section>



      {enrolOpen && (

        <section className="rounded border bg-white p-4 space-y-3">

          <div className="flex items-center justify-between">

            <h3 className="font-semibold text-slate-800">Enrol leads</h3>

            <button onClick={() => enrolMut.mutate()} disabled={selectedLeads.length === 0 || enrolMut.isPending} className="border rounded px-3 py-1.5 bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-slate-300">

              {enrolMut.isPending ? "Enrolling..." : `Enrol ${selectedLeads.length} selected`}

            </button>

          </div>

          {!leads?.items.length ? (

            <p className="text-sm text-slate-500">No leads. <Link to="/leads" className="text-sky-600 underline">Upload some first.</Link></p>

          ) : (

            <>

              <label className="text-xs text-slate-600">

                <input type="checkbox" checked={selectedLeads.length === leads.items.length} onChange={e => setSelectedLeads(e.target.checked ? leads.items.map(l => l.id) : [])} className="mr-2" />

                select all on this page ({leads.items.length})

              </label>

              <div className="border rounded overflow-y-auto max-h-96">

                <table className="w-full text-xs">

                  <thead className="bg-slate-50 text-left sticky top-0">

                    <tr><th></th><th>Name</th><th>Email</th><th>Company</th></tr>

                  </thead>

                  <tbody>

                    {leads.items.map(l => (

                      <tr key={l.id} className="border-t">

                        <td className="px-2 py-1"><input type="checkbox" checked={selectedLeads.includes(l.id)} onChange={e => setSelectedLeads(prev => e.target.checked ? [...prev, l.id] : prev.filter(x => x !== l.id))} /></td>

                        <td className="px-2 py-1">{[l.first_name, l.last_name].filter(Boolean).join(" ")}</td>

                        <td className="px-2 py-1 font-mono">{l.email}</td>

                        <td className="px-2 py-1">{l.company}</td>

                      </tr>

                    ))}

                  </tbody>

                </table>

              </div>

            </>

          )}

        </section>

      )}



      {editStep && (

        <StepEditModal

          sequenceId={sequenceId}

          step={editStep}

          aiKnowledgeId={seq.ai_knowledge_id}

          onClose={() => setSelectedStep(null)}

        />

      )}

    </div>

  );

}


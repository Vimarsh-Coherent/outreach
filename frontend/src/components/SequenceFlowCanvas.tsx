import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MiniMap,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getSmoothStepPath,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { StepOut } from "../api/sequences";
import { formatStepDelay, getStepChannelMeta, sortSteps } from "../lib/sequenceSteps";

// ── layout constants ─────────────────────────────────────────────────────────
const NODE_W = 300;
const X = 0;
const Y_TRIGGER = 0;
const Y_FIRST = 150;
const Y_GAP = 190;

// ── shared callback bag passed through node/edge `data` ──────────────────────
interface FlowCallbacks {
  onSelect: (step: StepOut) => void;
  onDelete: (stepId: number) => void;
  onInsert: (index: number) => void;
}

interface CanvasProps extends FlowCallbacks {
  steps: StepOut[];
  selectedStepId: number | null;
  /** Persist a new order (array of step ids, top→bottom). */
  onReorder: (orderedStepIds: number[]) => void;
}

// ── custom nodes ─────────────────────────────────────────────────────────────
function TriggerNode() {
  return (
    <div className="w-[300px] rounded-xl border-2 border-emerald-300 bg-emerald-50 px-4 py-3 shadow-sm">
      <div className="flex items-center gap-2">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-600 text-white">▶</span>
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-emerald-700">Trigger</p>
          <p className="text-sm font-medium text-emerald-900">When a lead is enrolled</p>
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-emerald-500" />
    </div>
  );
}

interface StepNodeData extends FlowCallbacks {
  step: StepOut;
  order: number;
  [key: string]: unknown;
}

function StepNode({ data, selected }: NodeProps) {
  const { step, order, onSelect, onDelete } = data as StepNodeData;
  const meta = getStepChannelMeta(step.channel);
  const preview = (step.subject?.trim() || step.body.trim()).slice(0, 70);
  const previewSuffix = (step.subject?.trim() || step.body.trim()).length > 70 ? "…" : "";

  return (
    <div
      className={`w-[300px] rounded-xl border bg-white shadow-sm transition-shadow hover:shadow-md ${
        selected ? "border-sky-500 ring-2 ring-sky-200" : "border-slate-200"
      }`}
    >
      <Handle type="target" position={Position.Top} className="!bg-slate-400" />
      <div className={`seq-drag-handle flex cursor-grab items-center justify-between gap-2 rounded-t-xl border-b px-3 py-2 active:cursor-grabbing select-none ${meta.accent}`}>
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-base opacity-50" aria-hidden title="Drag to reorder">⠿</span>
          <span className="text-lg" aria-hidden>{meta.icon}</span>
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-wide opacity-70">Step {order}</p>
            <p className="truncate text-sm font-semibold">{meta.label}</p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={() => onSelect(step)}
            className="nodrag rounded p-1 text-slate-500 hover:bg-white/70 hover:text-sky-600"
            title="Edit step"
          >✏️</button>
          <button
            type="button"
            onClick={() => { if (confirm(`Delete step ${order}?`)) onDelete(step.id); }}
            className="nodrag rounded p-1 text-slate-500 hover:bg-white/70 hover:text-rose-600"
            title="Delete step"
          >🗑</button>
        </div>
      </div>
      <button type="button" onClick={() => onSelect(step)} className="block w-full px-3 py-2 text-left">
        {step.subject && (
          <p className="truncate text-[11px] font-medium text-slate-600">Subject: {step.subject}</p>
        )}
        <p className="mt-0.5 text-[11px] leading-snug text-slate-500 line-clamp-2">
          {preview ? `${preview}${previewSuffix}` : <span className="italic text-slate-400">empty</span>}
        </p>
      </button>
      <Handle type="source" position={Position.Bottom} className="!bg-slate-400" />
    </div>
  );
}

const nodeTypes = { trigger: TriggerNode, step: StepNode };

// ── custom edge with a wait-label + "insert step here" button ────────────────
interface InsertEdgeData {
  delayLabel: string | null;
  insertIndex: number;
  onInsert: (index: number) => void;
  [key: string]: unknown;
}

function InsertEdge({ id, sourceX, sourceY, targetX, targetY, data }: EdgeProps) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX, sourceY, targetX, targetY,
    sourcePosition: Position.Bottom, targetPosition: Position.Top,
  });
  const { delayLabel, insertIndex, onInsert } = (data ?? {}) as InsertEdgeData;
  return (
    <>
      <BaseEdge id={id} path={path} style={{ stroke: "#cbd5e1", strokeWidth: 2 }} />
      <EdgeLabelRenderer>
        <div
          className="nodrag nopan absolute flex items-center gap-1.5"
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
        >
          {delayLabel ? (
            <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-800 whitespace-nowrap">
              ⏱ wait {delayLabel}
            </span>
          ) : (
            <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10px] text-slate-400 whitespace-nowrap">
              no wait
            </span>
          )}
          <button
            type="button"
            onClick={() => onInsert(insertIndex)}
            title="Insert a step here"
            className="flex h-5 w-5 items-center justify-center rounded-full border border-sky-300 bg-white text-sky-600 shadow-sm hover:bg-sky-50"
          >+</button>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

const edgeTypes = { insert: InsertEdge };

// ── layout: build nodes + edges from the linear step list ────────────────────
function buildGraph(
  steps: StepOut[],
  selectedStepId: number | null,
  cb: FlowCallbacks,
): { nodes: Node[]; edges: Edge[] } {
  const sorted = sortSteps(steps);
  const nodes: Node[] = [
    {
      id: "trigger",
      type: "trigger",
      position: { x: X, y: Y_TRIGGER },
      data: {},
      draggable: false,
      selectable: false,
    },
  ];
  const edges: Edge[] = [];

  sorted.forEach((step, i) => {
    const id = `step-${step.id}`;
    nodes.push({
      id,
      type: "step",
      position: { x: X, y: Y_FIRST + i * Y_GAP },
      data: { step, order: i + 1, ...cb },
      selected: selectedStepId === step.id,
      dragHandle: ".seq-drag-handle",
    });
    const sourceId = i === 0 ? "trigger" : `step-${sorted[i - 1].id}`;
    edges.push({
      id: `e-${sourceId}-${id}`,
      source: sourceId,
      target: id,
      type: "insert",
      data: {
        // The wait shown on the connector is the delay BEFORE the step it points to.
        delayLabel: formatStepDelay(step.delay_days, step.delay_hours),
        insertIndex: i,
        onInsert: cb.onInsert,
      },
    });
  });
  return { nodes, edges };
}

function FlowInner({
  steps, selectedStepId, onSelect, onDelete, onInsert, onReorder, fullscreen,
}: CanvasProps & { fullscreen: boolean }) {
  const rf = useReactFlow();
  // Re-fit when the container resizes (entering/leaving fullscreen).
  useEffect(() => {
    const t = setTimeout(() => rf.fitView({ padding: 0.2, duration: 200 }), 80);
    return () => clearTimeout(t);
  }, [fullscreen, rf]);

  // Keep latest callbacks in a ref so the node/edge `data` wrappers stay stable
  // across renders — otherwise the graph (and node positions) would rebuild on
  // every parent render, disrupting an in-progress drag.
  const cbRef = useRef({ onSelect, onDelete, onInsert, onReorder });
  cbRef.current = { onSelect, onDelete, onInsert, onReorder };

  const stableCb = useMemo<FlowCallbacks>(() => ({
    onSelect: (s) => cbRef.current.onSelect(s),
    onDelete: (id) => cbRef.current.onDelete(id),
    onInsert: (i) => cbRef.current.onInsert(i),
  }), []);

  // Structural signature: only rebuild the graph when step order/content or the
  // selection actually changes (not on unrelated parent re-renders).
  const sig = useMemo(
    () =>
      sortSteps(steps)
        .map(s => `${s.id}:${s.step_order}:${s.channel}:${s.delay_days}:${s.delay_hours}:${(s.subject ?? "").length}:${s.body.length}`)
        .join("|") + `#${selectedStepId}`,
    [steps, selectedStepId],
  );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const graph = useMemo(() => buildGraph(steps, selectedStepId, stableCb), [sig, stableCb]);

  const [nodes, setNodes, onNodesChange] = useNodesState(graph.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(graph.edges);

  // Re-sync the canvas whenever the underlying steps / selection change
  // (after add / edit / delete / reorder refetches).
  useEffect(() => {
    setNodes(graph.nodes);
    setEdges(graph.edges);
  }, [graph, setNodes, setEdges]);

  // Drag-to-reorder. Steps sit in evenly-spaced slots, so the target slot for a
  // dragged node is just the nearest slot index (round). This makes "drag step 1
  // onto step 2" reliably land at index 1 — no fragile y-tie ambiguity.
  const committedOrder = useMemo(() => sortSteps(steps).map(s => s.id), [steps]);

  const slotIndexFor = useCallback(
    (y: number) => {
      const n = committedOrder.length;
      return Math.max(0, Math.min(n - 1, Math.round((y - Y_FIRST) / Y_GAP)));
    },
    [committedOrder],
  );

  // While dragging, push the OTHER nodes into the slots that open up a gap at the
  // dragged node's target index (the live n8n-style reflow).
  const onNodeDrag = useCallback(
    (_evt: MouseEvent | TouchEvent, node: Node) => {
      if (node.type !== "step") return;
      const draggedId = Number((node.data as StepNodeData).step.id);
      const target = slotIndexFor(node.position.y);
      const others = committedOrder.filter(id => id !== draggedId);
      setNodes(curr =>
        curr.map(nd => {
          if (nd.type !== "step") return nd;
          const id = Number((nd.data as StepNodeData).step.id);
          if (id === draggedId) return nd; // follows the cursor
          const oi = others.indexOf(id);
          const y = Y_FIRST + (oi < target ? oi : oi + 1) * Y_GAP;
          return nd.position.y === y ? nd : { ...nd, position: { x: X, y } };
        }),
      );
    },
    [committedOrder, slotIndexFor, setNodes],
  );

  const onNodeDragStop = useCallback(
    (_evt: MouseEvent | TouchEvent, node: Node) => {
      if (node.type !== "step") {
        setNodes(graph.nodes);
        return;
      }
      const draggedId = Number((node.data as StepNodeData).step.id);
      const target = slotIndexFor(node.position.y);
      const others = committedOrder.filter(id => id !== draggedId);
      const newOrder = [...others.slice(0, target), draggedId, ...others.slice(target)];
      const changed = newOrder.some((id, i) => id !== committedOrder[i]);
      if (changed) {
        cbRef.current.onReorder(newOrder); // optimistic parent update re-lays out
      } else {
        setNodes(graph.nodes); // snap back cleanly
      }
    },
    [committedOrder, slotIndexFor, graph, setNodes],
  );

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeDrag={onNodeDrag}
      onNodeDragStop={onNodeDragStop}
      fitView
      fitViewOptions={{ padding: 0.25 }}
      minZoom={0.3}
      maxZoom={1.5}
      proOptions={{ hideAttribution: true }}
      nodesConnectable={false}
      panOnScroll
      selectionOnDrag={false}
    >
      <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#e2e8f0" />
      <MiniMap pannable zoomable nodeColor={(n) => (n.type === "trigger" ? "#34d399" : "#bae6fd")} />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

/** n8n-style node-graph canvas for an outreach sequence (vertical, linear). */
export default function SequenceFlowCanvas(props: CanvasProps) {
  const [fullscreen, setFullscreen] = useState(false);

  // Esc exits fullscreen.
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFullscreen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  return (
    <div
      className={
        fullscreen
          ? "fixed inset-0 z-50 bg-slate-50"
          : "relative h-[600px] w-full rounded-lg border bg-slate-50"
      }
    >
      <button
        type="button"
        onClick={() => setFullscreen(f => !f)}
        className={
          fullscreen
            ? "absolute right-4 top-4 z-10 flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-card hover:bg-slate-50"
            : "absolute right-3 top-3 z-10 flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-medium text-white shadow-card hover:bg-brand-700"
        }
        title={fullscreen ? "Exit fullscreen (Esc)" : "Expand the flow to fullscreen"}
      >
        {fullscreen ? "✕  Exit fullscreen" : "⛶  Fullscreen"}
      </button>
      <ReactFlowProvider>
        <FlowInner {...props} fullscreen={fullscreen} />
      </ReactFlowProvider>
    </div>
  );
}

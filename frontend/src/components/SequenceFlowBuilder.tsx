import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  DndContext,
  DragEndEvent,
  DragOverEvent,
  DragOverlay,
  DragStartEvent,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  rectIntersection,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { restrictToHorizontalAxis } from "@dnd-kit/modifiers";
import {
  SortableContext,
  arrayMove,
  horizontalListSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useEffect, useMemo, useRef, useState } from "react";

import type { SequenceDetail, StepOut } from "../api/sequences";
import { reorderSteps } from "../api/sequences";
import {
  formatStepDelay,
  getStepChannelMeta,
  hasStepDelay,
  reorderStepsWithTransitionDelays,
  sortSteps,
} from "../lib/sequenceSteps";

type DndStepId = string;

function stepDndId(stepId: number): DndStepId {
  return String(stepId);
}

function stepIdFromDnd(id: DndStepId | number): number {
  return Number(id);
}

function findStepIndex(items: StepOut[], dndId: DndStepId | number): number {
  const numericId = stepIdFromDnd(dndId);
  return items.findIndex(s => s.id === numericId);
}

interface FlowProps {
  steps: StepOut[];
  selectedStepId: number | null;
  onSelectStep: (step: StepOut) => void;
}

interface Props extends FlowProps {
  sequenceId: number;
}

/** Read-only horizontal flow diagram (AI preview, no drag/reorder). */
export function SequenceFlowPreview({ steps, selectedStepId, onSelectStep }: FlowProps) {
  const sorted = useMemo(() => sortSteps(steps), [steps]);

  if (sorted.length === 0) {
    return (
      <section className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center">
        <p className="text-sm font-medium text-slate-700">No steps generated yet</p>
      </section>
    );
  }

  return (
    <section className="rounded-lg border bg-white shadow-sm overflow-hidden">
      <div className="border-b bg-slate-50 px-4 py-2.5">
        <h3 className="text-sm font-semibold text-slate-800">Sequence flow</h3>
        <p className="text-xs text-slate-500 mt-0.5">
          Generated outreach flow · Click a step to edit below
        </p>
      </div>
      <div className="overflow-x-auto overscroll-x-contain">
        <div className="inline-flex items-center min-w-full px-4 py-8">
          {sorted.map((step, index) => {
            const delayAfterStep =
              index < sorted.length - 1
                ? formatStepDelay(sorted[index + 1].delay_days, sorted[index + 1].delay_hours)
                : null;
            const delayBeforeStep = formatStepDelay(step.delay_days, step.delay_hours);
            const showEnrolmentWait = index === 0 && hasStepDelay(step.delay_days, step.delay_hours);
            return (
              <StaticFlowItem
                key={step.id}
                step={step}
                displayOrder={index + 1}
                selected={selectedStepId === step.id}
                onSelect={() => onSelectStep(step)}
                trailingConnectorDelay={delayAfterStep}
                leadingDelay={showEnrolmentWait ? delayBeforeStep : null}
              />
            );
          })}
        </div>
      </div>
    </section>
  );
}

export default function SequenceFlowBuilder({
  sequenceId,
  steps,
  selectedStepId,
  onSelectStep,
}: Props) {
  const qc = useQueryClient();
  const sorted = useMemo(() => sortSteps(steps), [steps]);
  const sortedRef = useRef(sorted);
  const [flowSteps, setFlowSteps] = useState<StepOut[]>(sorted);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [reorderError, setReorderError] = useState<string | null>(null);
  const flowStepsRef = useRef(flowSteps);
  const lastAttemptedIdsRef = useRef<number[] | null>(null);

  useEffect(() => {
    sortedRef.current = sorted;
  }, [sorted]);

  useEffect(() => {
    if (activeId == null) setFlowSteps(sorted);
  }, [sorted, activeId]);

  useEffect(() => {
    flowStepsRef.current = flowSteps;
  }, [flowSteps]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const reorderMut = useMutation({
    mutationFn: (stepIds: number[]) => reorderSteps(sequenceId, stepIds),
    onMutate: async stepIds => {
      setReorderError(null);
      lastAttemptedIdsRef.current = stepIds;
      await qc.cancelQueries({ queryKey: ["sequence", sequenceId] });
      const prev = qc.getQueryData<SequenceDetail>(["sequence", sequenceId]);
      if (prev) {
        qc.setQueryData<SequenceDetail>(["sequence", sequenceId], {
          ...prev,
          steps: reorderStepsWithTransitionDelays(prev.steps, stepIds),
        });
      }
      return { prev };
    },
    onSuccess: updatedSteps => {
      setReorderError(null);
      qc.setQueryData<SequenceDetail>(["sequence", sequenceId], prev =>
        prev ? { ...prev, steps: sortSteps(updatedSteps) } : prev,
      );
    },
    onError: (err, _ids, ctx) => {
      if (ctx?.prev) qc.setQueryData(["sequence", sequenceId], ctx.prev);
      const attempted = lastAttemptedIdsRef.current;
      if (attempted?.length) {
        setFlowSteps(reorderStepsWithTransitionDelays(sortedRef.current, attempted));
      } else {
        setFlowSteps(sortedRef.current);
      }
      const status = (err as { response?: { status?: number } })?.response?.status;
      const hint =
        status === 500
          ? " Save failed (server error). Check backend is running on port 8001."
          : "";
      setReorderError(`Could not save new order — try again.${hint}`);
    },
  });

  function retryReorder() {
    const ids = lastAttemptedIdsRef.current;
    if (!ids?.length || reorderMut.isPending) return;
    setReorderError(null);
    setFlowSteps(reorderStepsWithTransitionDelays(sortedRef.current, ids));
    reorderMut.mutate(ids);
  }

  const activeStep = activeId != null ? flowSteps.find(s => s.id === activeId) : null;
  const activeIndex = activeStep ? flowSteps.findIndex(s => s.id === activeStep.id) : -1;

  function handleDragStart(event: DragStartEvent) {
    setReorderError(null);
    setActiveId(stepIdFromDnd(String(event.active.id)));
  }

  function handleDragOver(event: DragOverEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    setFlowSteps(items => {
      const oldIndex = findStepIndex(items, String(active.id));
      const newIndex = findStepIndex(items, String(over.id));
      if (oldIndex < 0 || newIndex < 0 || oldIndex === newIndex) return items;
      return arrayMove(items, oldIndex, newIndex);
    });
  }

  function handleDragEnd(_event: DragEndEvent) {
    setActiveId(null);

    const nextIds = flowStepsRef.current.map(s => s.id);
    const prevIds = sortedRef.current.map(s => s.id);
    const changed = nextIds.some((id, i) => id !== prevIds[i]);

    if (!changed) {
      setFlowSteps(sortedRef.current);
      return;
    }

    const remapped = reorderStepsWithTransitionDelays(sortedRef.current, nextIds);
    setFlowSteps(remapped);
    reorderMut.mutate(nextIds);
  }

  function handleDragCancel() {
    setActiveId(null);
    setFlowSteps(sortedRef.current);
  }

  if (flowSteps.length === 0) {
    return (
      <section className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center">
        <p className="text-sm font-medium text-slate-700">No steps in this sequence yet</p>
        <p className="text-xs text-slate-500 mt-1">
          Add a step below to start building your outreach flow.
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-lg border bg-white shadow-sm overflow-hidden">
      <div className="flex items-center justify-between border-b bg-slate-50 px-4 py-2.5">
        <div>
          <h3 className="text-sm font-semibold text-slate-800">Sequence flow</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Drag cards to reorder · Waits follow flow order · Click to edit
          </p>
        </div>
        {reorderMut.isPending && (
          <span className="text-xs text-sky-600 animate-pulse">Saving order…</span>
        )}
      </div>

      {reorderError && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rose-200 bg-rose-50 px-4 py-2 text-xs text-rose-700">
          <span>{reorderError}</span>
          <button
            type="button"
            onClick={retryReorder}
            disabled={reorderMut.isPending}
            className="rounded border border-rose-300 bg-white px-2 py-0.5 text-rose-800 hover:bg-rose-100 disabled:opacity-50"
          >
            Retry save
          </button>
        </div>
      )}

      <DndContext
        sensors={sensors}
        collisionDetection={rectIntersection}
        modifiers={[restrictToHorizontalAxis]}
        onDragStart={handleDragStart}
        onDragOver={handleDragOver}
        onDragEnd={handleDragEnd}
        onDragCancel={handleDragCancel}
      >
        <div className="overflow-x-auto overscroll-x-contain">
          <SortableContext
            items={flowSteps.map(s => stepDndId(s.id))}
            strategy={horizontalListSortingStrategy}
          >
            {/* Delay on step N = wait before step N runs; remapped on reorder. */}
            <div className="inline-flex items-center min-w-full px-4 py-8">
              {flowSteps.map((step, index) => {
                const delayBeforeStep = formatStepDelay(step.delay_days, step.delay_hours);
                const delayAfterStep =
                  index < flowSteps.length - 1
                    ? formatStepDelay(flowSteps[index + 1].delay_days, flowSteps[index + 1].delay_hours)
                    : null;
                const showEnrolmentWait = index === 0 && hasStepDelay(step.delay_days, step.delay_hours);
                return (
                  <SortableFlowItem
                    key={step.id}
                    step={step}
                    displayOrder={index + 1}
                    selected={selectedStepId === step.id}
                    onSelect={() => onSelectStep(step)}
                    isActive={activeId === step.id}
                    trailingConnectorDelay={delayAfterStep}
                    leadingDelay={showEnrolmentWait ? delayBeforeStep : null}
                  />
                );
              })}
            </div>
          </SortableContext>
        </div>

        <DragOverlay dropAnimation={{ duration: 200, easing: "ease-out" }}>
          {activeStep && activeIndex >= 0 ? (
            <FlowItemPreview
              step={activeStep}
              displayOrder={activeIndex + 1}
              trailingConnectorDelay={
                activeIndex < flowSteps.length - 1
                  ? formatStepDelay(
                      flowSteps[activeIndex + 1].delay_days,
                      flowSteps[activeIndex + 1].delay_hours,
                    )
                  : null
              }
            />
          ) : null}
        </DragOverlay>
      </DndContext>
    </section>
  );
}

function StaticFlowItem({
  step,
  displayOrder,
  selected,
  onSelect,
  trailingConnectorDelay,
  leadingDelay,
}: {
  step: StepOut;
  displayOrder: number;
  selected: boolean;
  onSelect: () => void;
  trailingConnectorDelay: string | null;
  leadingDelay: string | null;
}) {
  return (
    <div className="flex items-center shrink-0">
      <div className="relative w-44 sm:w-48 shrink-0">
        {leadingDelay && (
          <div className="absolute -top-6 left-1/2 -translate-x-1/2 z-10" title="Wait after enrolment before first step">
            <DelayChip label={leadingDelay} />
          </div>
        )}
        <button
          type="button"
          onClick={onSelect}
          className={`w-full rounded-lg border bg-white shadow-sm transition-shadow duration-150 hover:shadow-md text-left ${
            selected
              ? "border-sky-500 ring-2 ring-sky-200"
              : "border-slate-200 hover:border-slate-300"
          }`}
        >
          <div className="p-3">
            <StepCardContent step={step} displayOrder={displayOrder} />
          </div>
        </button>
      </div>
      <FlowConnector delay={trailingConnectorDelay} />
    </div>
  );
}

function SortableFlowItem({
  step,
  displayOrder,
  selected,
  onSelect,
  isActive,
  trailingConnectorDelay,
  leadingDelay,
}: {
  step: StepOut;
  displayOrder: number;
  selected: boolean;
  onSelect: () => void;
  isActive: boolean;
  trailingConnectorDelay: string | null;
  leadingDelay: string | null;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: stepDndId(step.id) });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`flex items-center shrink-0 touch-none ${isDragging ? "z-20 opacity-30" : ""}`}
    >
      <div className="relative w-44 sm:w-48 shrink-0">
        {leadingDelay && (
          <div className="absolute -top-6 left-1/2 -translate-x-1/2 z-10" title="Wait after enrolment before first step">
            <DelayChip label={leadingDelay} />
          </div>
        )}
        <div
          className={`group rounded-lg border bg-white shadow-sm transition-shadow duration-150 hover:shadow-md ${
            selected
              ? "border-sky-500 ring-2 ring-sky-200"
              : "border-slate-200 hover:border-slate-300"
          } ${isActive ? "ring-2 ring-sky-300" : ""}`}
        >
          <div
            {...attributes}
            {...listeners}
            className="flex cursor-grab active:cursor-grabbing items-center gap-1 border-b border-slate-100 bg-slate-50/80 px-2 py-1 rounded-t-lg select-none"
            aria-label={`Drag step ${displayOrder}`}
          >
            <GripIcon />
            <span className="text-[10px] font-medium text-slate-400 uppercase tracking-wide">
              Drag
            </span>
          </div>
          <button type="button" onClick={onSelect} className="w-full text-left p-3 pt-2">
            <StepCardContent step={step} displayOrder={displayOrder} />
          </button>
        </div>
      </div>
      <FlowConnector delay={trailingConnectorDelay} />
    </div>
  );
}

function FlowItemPreview({
  step,
  displayOrder,
  trailingConnectorDelay,
}: {
  step: StepOut;
  displayOrder: number;
  trailingConnectorDelay: string | null;
}) {
  return (
    <div className="flex items-center shrink-0">
      <div className="w-44 sm:w-48 rounded-lg border border-sky-300 bg-white shadow-xl ring-2 ring-sky-200 rotate-1">
        <div className="flex items-center gap-1 border-b border-sky-100 bg-sky-50 px-2 py-1">
          <GripIcon />
          <span className="text-[10px] font-medium text-sky-600 uppercase">Dragging</span>
        </div>
        <div className="p-3 pt-2">
          <StepCardContent step={step} displayOrder={displayOrder} />
        </div>
      </div>
      <FlowConnector delay={trailingConnectorDelay} />
    </div>
  );
}

function StepCardContent({ step, displayOrder }: { step: StepOut; displayOrder: number }) {
  const meta = getStepChannelMeta(step.channel);
  const preview = (step.subject?.trim() || step.body.trim()).slice(0, 48);
  const previewSuffix = (step.subject?.trim() || step.body.trim()).length > 48 ? "…" : "";

  return (
    <>
      <div className="flex items-start gap-2">
        <div
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border text-lg ${meta.accent}`}
        >
          <span aria-hidden>{meta.icon}</span>
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
            Step {displayOrder}
          </p>
          <p className="text-xs font-semibold text-slate-800 leading-tight line-clamp-2">
            {meta.label}
          </p>
        </div>
      </div>
      {preview && (
        <p className="mt-2 text-[11px] text-slate-500 line-clamp-2 leading-snug">
          {step.subject ? `"${preview}${previewSuffix}"` : `${preview}${previewSuffix}`}
        </p>
      )}
    </>
  );
}

function FlowConnector({ delay }: { delay: string | null }) {
  return (
    <div className="flex flex-col items-center justify-center w-12 sm:w-16 shrink-0 px-0.5 pointer-events-none">
      {delay ? (
        <DelayChip label={delay} />
      ) : (
        <span className="h-[18px]" aria-hidden />
      )}
      <div className="flex w-full items-center mt-1">
        <div className="h-px flex-1 bg-slate-300" />
        {delay ? <ArrowIcon /> : <span className="w-3 shrink-0" aria-hidden />}
        <div className="h-px flex-1 bg-slate-300" />
      </div>
    </div>
  );
}

function DelayChip({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-0.5 rounded-full bg-amber-50 border border-amber-200 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 whitespace-nowrap">
      <span aria-hidden>⏱</span>
      {label}
    </span>
  );
}

function GripIcon() {
  return (
    <svg className="h-3.5 w-3.5 text-slate-400 shrink-0" viewBox="0 0 12 12" fill="currentColor" aria-hidden>
      <circle cx="3" cy="2" r="1" />
      <circle cx="9" cy="2" r="1" />
      <circle cx="3" cy="6" r="1" />
      <circle cx="9" cy="6" r="1" />
      <circle cx="3" cy="10" r="1" />
      <circle cx="9" cy="10" r="1" />
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg className="h-3 w-3 text-slate-400 shrink-0 mx-0.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden>
      <path
        fillRule="evenodd"
        d="M3 10a.75.75 0 01.75-.75h10.638L10.23 5.29a.75.75 0 111.04-1.08l5.5 5.25a.75.75 0 010 1.08l-5.5 5.25a.75.75 0 11-1.04-1.08l4.158-3.96H3.75A.75.75 0 013 10z"
        clipRule="evenodd"
      />
    </svg>
  );
}

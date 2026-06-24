export const DATE_RANGES = [
  { label: "Yesterday",     days: 1 },
  { label: "Last 7 days",   days: 7 },
  { label: "Last 30 days",  days: 30 },
  { label: "Last 3 months", days: 90 },
  { label: "Last 6 months", days: 180 },
  { label: "Last 1 year",   days: 365 },
  { label: "All time",      days: 0 },
] as const;

export type DateRangeDays = typeof DATE_RANGES[number]["days"];

export function DateRangeSelect({
  value,
  onChange,
}: {
  value: number;
  onChange: (days: number) => void;
}) {
  return (
    <select
      value={value}
      onChange={e => onChange(Number(e.target.value))}
      className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 bg-white text-slate-700 shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500 cursor-pointer"
    >
      {DATE_RANGES.map(r => (
        <option key={r.days} value={r.days}>{r.label}</option>
      ))}
    </select>
  );
}

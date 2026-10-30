import { useEffect, useRef, useState } from "react";
import { type DateRange, type RangePreset, PRESETS, defaultRange, isValidRange, rangeLabel, resolveRange, toLocalInput } from "./dateRange";

/** Button + popover for choosing a preset or a custom date-time range (see ./dateRange). */
export default function DateTimeRangePicker({
  value,
  onChange,
  label = "Date range",
}: {
  value: DateRange;
  onChange: (range: DateRange) => void;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  // Draft custom range, applied with the Apply button.
  const [from, setFrom] = useState(value.from);
  const [to, setTo] = useState(value.to);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const toggleOpen = () => {
    if (!open) {
      // Start the custom fields from the range currently shown.
      const { start, end } = resolveRange(value);
      setFrom(toLocalInput(start));
      setTo(toLocalInput(end));
    }
    setOpen(!open);
  };

  const draft: DateRange = { preset: "custom", from, to };
  const draftValid = Boolean(from && to) && isValidRange(draft);

  const choosePreset = (preset: Exclude<RangePreset, "custom">) => {
    onChange(defaultRange(preset));
    setOpen(false);
  };

  const applyCustom = () => {
    if (!draftValid) return;
    onChange(draft);
    setOpen(false);
  };

  const inputClass =
    "w-full rounded-md border border-slate-600 bg-slate-900 px-2 py-1.5 text-sm text-slate-100 [color-scheme:dark] focus:border-indigo-500 focus:outline-none";

  return (
    <div className="relative" ref={rootRef}>
      <button
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={label}
        className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 transition hover:border-slate-500"
        onClick={toggleOpen}
        title="Date and time range for the dashboard and for Sync"
        type="button"
      >
        <svg aria-hidden="true" className="h-4 w-4 text-slate-400" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
          <rect height="18" rx="2" width="18" x="3" y="4" />
          <path d="M16 2v4M8 2v4M3 10h18" />
        </svg>
        <span className="max-w-[18rem] truncate">{rangeLabel(value)}</span>
        <span aria-hidden="true" className="text-xs text-slate-500">▾</span>
      </button>

      {open && (
        <div
          aria-label="Choose a date and time range"
          className="absolute right-0 z-30 mt-2 flex w-[min(34rem,calc(100vw-2rem))] flex-col gap-4 rounded-xl border border-slate-700 bg-slate-900 p-4 shadow-2xl shadow-black/40 sm:flex-row"
          role="dialog"
        >
          <div className="flex flex-row flex-wrap gap-1 sm:w-40 sm:flex-col">
            {PRESETS.map((preset) => (
              <button
                className={`rounded-md px-3 py-1.5 text-left text-sm transition ${
                  value.preset === preset.key ? "bg-indigo-500 text-white" : "text-slate-300 hover:bg-slate-800"
                }`}
                key={preset.key}
                onClick={() => choosePreset(preset.key)}
                type="button"
              >
                {preset.label}
              </button>
            ))}
          </div>
          <div className="flex flex-1 flex-col gap-3 border-slate-800 sm:border-l sm:pl-4">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Custom range</p>
            <label className="flex flex-col gap-1 text-xs text-slate-400">
              From
              <input aria-label="Range start" className={inputClass} onChange={(e) => setFrom(e.target.value)} type="datetime-local" value={from} />
            </label>
            <label className="flex flex-col gap-1 text-xs text-slate-400">
              To
              <input aria-label="Range end" className={inputClass} onChange={(e) => setTo(e.target.value)} type="datetime-local" value={to} />
            </label>
            {!draftValid && from && to && <p className="text-xs text-rose-300">“From” must be before “To”.</p>}
            <p className="text-xs text-slate-500">
              Times are in your local time zone ({Intl.DateTimeFormat().resolvedOptions().timeZone}).
            </p>
            <div className="flex justify-end gap-2">
              <button className="rounded-md px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800" onClick={() => setOpen(false)} type="button">
                Cancel
              </button>
              <button
                className="rounded-md bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
                disabled={!draftValid}
                onClick={applyCustom}
                type="button"
              >
                Apply
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

import { useEffect, useRef, useState } from "react";

export interface BranchOption {
  name: string;
  pull_requests: number;
}

/**
 * Multi-select dropdown for PR target (base) branches.
 * An empty selection means "all branches".
 */
export default function BranchSelect({
  options,
  unknown = 0,
  selected,
  onChange,
}: {
  options: BranchOption[];
  /** PRs whose target branch is not recorded yet (synced before branches were stored). */
  unknown?: number;
  selected: string[];
  onChange: (branches: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Keep selected branches visible even if they have no PRs in the current window.
  const known = new Set(options.map((option) => option.name));
  const all = [...options, ...selected.filter((name) => !known.has(name)).map((name) => ({ name, pull_requests: 0 }))];
  const visible = all.filter((option) => option.name.toLowerCase().includes(search.toLowerCase()));

  const label =
    selected.length === 0
      ? "All branches"
      : selected.length <= 2
        ? selected.join(", ")
        : `${selected.length} branches`;

  const toggle = (name: string) =>
    onChange(selected.includes(name) ? selected.filter((item) => item !== name) : [...selected, name]);

  return (
    <div className="relative" ref={rootRef}>
      <button
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label="Target branch filter"
        className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm focus:outline-none ${
          selected.length ? "border-indigo-500 bg-indigo-500/10 text-indigo-100" : "border-slate-700 bg-slate-800 text-slate-200"
        }`}
        onClick={() => setOpen(!open)}
        title="Only count pull requests that target these branches"
        type="button"
      >
        <span className="text-slate-400">Target branch:</span>
        <span className="max-w-[16rem] truncate">{label}</span>
        <span aria-hidden="true" className="text-xs text-slate-400">
          ▾
        </span>
      </button>

      {open && (
        <div className="absolute left-0 z-20 mt-2 w-72 rounded-xl border border-slate-700 bg-slate-900 p-2 shadow-2xl shadow-black/40">
          <input
            aria-label="Search branches"
            autoFocus
            className="mb-2 w-full rounded-lg border border-slate-700 bg-slate-800 px-2 py-1.5 text-sm text-slate-200 focus:outline-none"
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search branches…"
            value={search}
          />
          <button
            className={`mb-1 w-full rounded-lg px-2 py-1.5 text-left text-sm ${
              selected.length === 0 ? "bg-slate-800 text-white" : "text-slate-300 hover:bg-slate-800"
            }`}
            onClick={() => onChange([])}
            type="button"
          >
            All branches
          </button>
          <ul aria-multiselectable="true" className="max-h-64 overflow-y-auto" role="listbox">
            {visible.map((option) => (
              <li key={option.name}>
                <label className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-slate-200 hover:bg-slate-800">
                  <input
                    checked={selected.includes(option.name)}
                    className="h-4 w-4 accent-indigo-500"
                    onChange={() => toggle(option.name)}
                    type="checkbox"
                  />
                  <span className="flex-1 truncate" title={option.name}>
                    {option.name}
                  </span>
                  <span className="text-xs text-slate-500">{option.pull_requests}</span>
                </label>
              </li>
            ))}
            {visible.length === 0 && <li className="px-2 py-1.5 text-sm text-slate-500">No matching branches.</li>}
          </ul>
          {unknown > 0 && (
            <p className="mt-2 border-t border-slate-800 px-2 pt-2 text-xs text-amber-300">
              {unknown} PR(s) have no recorded target branch and are excluded when filtering. Run a sync with “Full refresh”
              to fill it in.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

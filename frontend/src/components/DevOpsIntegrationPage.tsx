import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import BranchSelect, { type BranchOption } from "./BranchSelect";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

// ---------------------------------------------------------------------------
// API types
// ---------------------------------------------------------------------------

type Level = "Elite" | "High" | "Medium" | "Low" | null;

interface DevOpsConfig {
  configured: boolean;
  missing: string[];
  organization: string;
  projects: string[];
  work_item_types: string[];
  deployment_pattern: string;
  production_pattern: string;
  incidents_enabled: boolean;
  work_items: number;
  pipeline_runs: number;
  last_sync: SyncRun | null;
}

interface SyncRun {
  lookback_days?: number;
  work_items?: number;
  pipeline_runs?: number;
  skipped_work_items?: number;
  skipped_identities?: { identity: string; work_items: number }[];
  azure_requests?: number;
  snapshot?: string;
  error?: string;
  finished_at?: string;
}

interface UnlinkedIdentity {
  identity: string;
  display_name: string | null;
  work_items: number;
}

interface FilterOptions {
  tags: { tag: string; count: number }[];
  people: {
    key: string;
    label: string;
    role: string | null;
    team: string | null;
    github: string | null;
    azure: string | null;
  }[];
  unlinked_identities: UnlinkedIdentity[];
  types: string[];
  teams: string[];
}

interface PeopleMapping {
  teams_enabled: boolean;
  people: {
    key: string;
    display_name: string;
    team: string;
    github: string | null;
    azure_devops: string[];
    role: string | null;
    pull_requests: number;
    work_items: number;
  }[];
  unlinked_azure_identities: UnlinkedIdentity[];
}

interface LinkedPr {
  repository: string;
  pr_number: number;
  author: string;
  merged_at: string | null;
}

interface Story {
  id: number;
  project: string;
  type: string;
  title: string;
  state: string | null;
  assignee: string | null;
  person_key: string | null;
  role: string | null;
  team: string | null;
  tags: string[];
  story_points: number | null;
  created_date: string | null;
  activated_date: string | null;
  closed_date: string | null;
  cycle_time_days: number | null;
  lead_time_days: number | null;
  linked_prs: LinkedPr[];
  url: string | null;
}

interface StorySummary {
  total: number;
  completed: number;
  open: number;
  story_points_completed: number | null;
  median_cycle_time_days: number | null;
  average_cycle_time_days: number | null;
  median_lead_time_days: number | null;
  throughput_per_week: number | null;
  by_type: Record<string, number>;
  by_tag: Record<string, number>;
  linked_pr_count: number;
}

interface EfficiencyRow {
  person_key: string;
  person: string;
  role: string | null;
  team: string | null;
  completed: number;
  story_points: number | null;
  median_cycle_time_days: number | null;
  throughput_per_week: number | null;
  share_of_completed_pct: number | null;
  bugs_reported: number;
  prs_merged: number;
}

interface StoriesResponse {
  summary: StorySummary;
  efficiency: EfficiencyRow[];
  stories: Story[];
}

interface DoraResponse {
  scope: { person: string | null; person_label: string | null; team: string | null; kind: "all" | "team" | "person" };
  method: "pipeline" | "incidents";
  deployment_frequency: {
    deployments: number;
    deployment_days: number;
    deployments_per_week: number | null;
    median_deployment_days_per_week: number | null;
    median_deployment_days_per_month: number | null;
    level: Level;
  };
  lead_time: { median_hours: number | null; average_hours: number | null; pull_requests: number; level: Level };
  change_failure_rate: { rate: number | null; failed: number; total: number; incidents: number | null; level: Level };
  recovery_time: { median_hours: number | null; samples: number; level: Level };
  weekly: { week: string; deployments: number; failed_deployments: number; median_lead_time_hours: number | null }[];
  benchmarks: Record<string, Record<string, string>>;
  pipelines: string[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { signal });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function daysAgo(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return isoDate(date);
}

function formatHours(hours: number | null | undefined): string {
  if (hours == null) return "—";
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  if (hours < 48) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} days`;
}

function formatDays(days: number | null | undefined): string {
  return days == null ? "—" : `${days.toFixed(1)} d`;
}

const LEVEL_STYLES: Record<string, string> = {
  Elite: "border-emerald-500/40 bg-emerald-500/15 text-emerald-300",
  High: "border-sky-500/40 bg-sky-500/15 text-sky-300",
  Medium: "border-amber-500/40 bg-amber-500/15 text-amber-300",
  Low: "border-rose-500/40 bg-rose-500/15 text-rose-300",
};

function Spinner({ label }: { label?: string }) {
  return (
    <span role="status" aria-label={label ?? "Loading"} className="inline-flex items-center gap-2 text-slate-400">
      <svg aria-hidden="true" className="h-5 w-5 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" fill="currentColor" />
      </svg>
      {label && <span className="text-sm">{label}</span>}
    </span>
  );
}

function LevelBadge({ level }: { level: Level }) {
  if (!level) return <span className="rounded-full border border-slate-700 px-2 py-0.5 text-xs text-slate-400">No data</span>;
  return <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${LEVEL_STYLES[level]}`}>{level}</span>;
}

function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl shadow-black/20 ${className}`}>
      {children}
    </div>
  );
}

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
      <p className="text-xs uppercase tracking-wide text-slate-400">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-slate-50">{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

const inputClass =
  "rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:border-indigo-500 focus:outline-none";

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function DevOpsIntegrationPage({
  branchOptions,
  selectedBranches,
  onBranchesChange,
  unknownBranchPrs,
}: {
  branchOptions: BranchOption[];
  selectedBranches: string[];
  onBranchesChange: (branches: string[]) => void;
  unknownBranchPrs: number;
}) {
  const [config, setConfig] = useState<DevOpsConfig | null>(null);
  const [options, setOptions] = useState<FilterOptions | null>(null);
  const [pageError, setPageError] = useState("");

  // Filters
  const [dateFrom, setDateFrom] = useState(daysAgo(29));
  const [dateTo, setDateTo] = useState(isoDate(new Date()));
  const [team, setTeam] = useState("");
  const [person, setPerson] = useState("");
  const [types, setTypes] = useState<string[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [tagMode, setTagMode] = useState<"any" | "all">("any");
  const [status, setStatus] = useState<"completed" | "all">("completed");
  const [tagSearch, setTagSearch] = useState("");

  // Data
  const [stories, setStories] = useState<StoriesResponse | null>(null);
  const [dora, setDora] = useState<DoraResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [dataError, setDataError] = useState("");

  // Sync
  const [syncDays, setSyncDays] = useState(30);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState("");

  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const loadMeta = useCallback(async () => {
    try {
      const [cfg, opts] = await Promise.all([
        getJson<DevOpsConfig>("/api/devops/config"),
        getJson<FilterOptions>("/api/devops/filters"),
      ]);
      setConfig(cfg);
      setOptions(opts);
      setPageError("");
    } catch (err) {
      setPageError(`Failed to load DevOps settings: ${(err as Error).message}`);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(loadMeta, 0);
    return () => clearTimeout(timer);
  }, [loadMeta]);

  const query = useMemo(() => {
    const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo, tag_mode: tagMode, status });
    tags.forEach((tag) => params.append("tags", tag));
    types.forEach((type) => params.append("types", type));
    if (team) params.set("team", team);
    if (person) params.set("person", person);
    selectedBranches.forEach((branch) => params.append("branches", branch));
    return params.toString();
  }, [dateFrom, dateTo, tagMode, status, tags, types, team, person, selectedBranches]);

  const loadData = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setDataError("");
    try {
      const [storyData, doraData] = await Promise.all([
        getJson<StoriesResponse>(`/api/devops/stories?${query}`, controller.signal),
        getJson<DoraResponse>(`/api/devops/dora?${query}`, controller.signal),
      ]);
      setStories(storyData);
      setDora(doraData);
    } catch (err) {
      if (isAbortError(err)) return;
      setStories(null);
      setDora(null);
      setDataError((err as Error).message);
    } finally {
      if (abortRef.current === controller) setLoading(false);
    }
  }, [query]);

  // Reload shortly after the filters stop changing.
  useEffect(() => {
    const timer = setTimeout(loadData, 300);
    return () => clearTimeout(timer);
  }, [loadData]);

  const handleSync = async () => {
    setSyncing(true);
    setSyncMessage("");
    setPageError("");
    try {
      const res = await fetch("/api/devops/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lookback_days: syncDays }),
      });
      const data = await res.json();
      if (!res.ok) {
        setPageError(data.detail ?? `Sync failed (${res.status}).`);
        return;
      }
      let run: SyncRun | null = null;
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        const status = await getJson<{ status: string; last_run: SyncRun | null }>("/api/devops/sync/status");
        run = status.last_run;
        if (status.status !== "running") break;
      }
      if (run?.error) {
        setPageError(`Azure DevOps sync failed: ${run.error}`);
      } else if (run) {
        setSyncMessage(
          `Synced ${run.work_items ?? 0} work item(s) and ${run.pipeline_runs ?? 0} pipeline run(s) ` +
            `with ${run.azure_requests ?? 0} Azure DevOps request(s)` +
            (run.skipped_work_items
              ? `; skipped ${run.skipped_work_items} item(s) of people not in a team (` +
                (run.skipped_identities ?? [])
                  .slice(0, 5)
                  .map((item) => item.identity)
                  .join(", ") +
                ") — link them under teams with azure_devops in config.yaml"
              : "") +
            ".",
        );
      }
      await loadMeta();
      await loadData();
    } catch {
      setPageError("Azure DevOps sync failed. Check backend logs.");
    } finally {
      setSyncing(false);
    }
  };

  const setRange = (days: number) => {
    setDateFrom(daysAgo(days - 1));
    setDateTo(isoDate(new Date()));
  };

  const toggle = (list: string[], value: string) =>
    list.includes(value) ? list.filter((item) => item !== value) : [...list, value];

  const clearFilters = () => {
    setTeam("");
    setPerson("");
    setTypes([]);
    setTags([]);
    setTagMode("any");
    setStatus("completed");
    setRange(30);
  };

  const people = (options?.people ?? []).filter((p) => !team || p.team === team);
  const visibleTags = (options?.tags ?? []).filter(
    (t) => !tagSearch || t.tag.toLowerCase().includes(tagSearch.toLowerCase()),
  );
  const personLabel = options?.people.find((p) => p.key === person)?.label ?? person;
  const scopeLabel = person ? personLabel : team ? `Team ${team}` : "All teams";
  const hasData = (config?.work_items ?? 0) + (config?.pipeline_runs ?? 0) > 0;

  return (
    <div className="space-y-6">
      {/* Connection */}
      <Card>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-400">Azure Boards + Azure Pipelines</p>
            <h2 className="mt-2 text-2xl font-semibold text-white">DevOps Integration</h2>
            <p className="mt-2 max-w-3xl text-sm text-slate-300">
              Story delivery and people efficiency from Azure Boards, and DORA metrics from Azure Pipelines deployments
              linked to GitHub pull requests (definitions follow Apache DevLake).
            </p>
            {config && (
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                {config.configured ? (
                  <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-emerald-300">
                    Connected: {config.organization} / {config.projects.join(", ")}
                  </span>
                ) : (
                  <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-3 py-1 text-amber-300">
                    Not configured — missing {config.missing.join(", ")}
                  </span>
                )}
                <span className="rounded-full border border-slate-700 px-3 py-1 text-slate-300">
                  {config.work_items} work items · {config.pipeline_runs} pipeline runs stored
                </span>
                <span className="rounded-full border border-slate-700 px-3 py-1 text-slate-300" title="config.yaml azure_devops.deployment_pattern / production_pattern">
                  Deployments: /{config.deployment_pattern}/{config.production_pattern ? ` + /${config.production_pattern}/` : ""}
                </span>
              </div>
            )}
          </div>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200">
              <span>Last</span>
              <input
                aria-label="Azure sync days"
                className="w-16 rounded-md border border-slate-600 bg-slate-900 px-2 py-1 text-right focus:outline-none"
                min={1}
                max={730}
                onChange={(e) => setSyncDays(Number(e.target.value) || 30)}
                type="number"
                value={syncDays}
              />
              <span className="text-xs uppercase tracking-wide text-slate-400">days</span>
            </label>
            <button
              className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-sky-500 disabled:opacity-50"
              disabled={syncing || !config?.configured}
              onClick={handleSync}
              title={config?.configured ? "Fetch work items and pipeline runs from Azure DevOps" : "Configure Azure DevOps first"}
              type="button"
            >
              {syncing ? "Syncing…" : "⟳ Sync Azure DevOps"}
            </button>
          </div>
        </div>
        {syncMessage && <p className="mt-4 rounded-lg bg-emerald-900/40 px-4 py-2 text-sm text-emerald-300">{syncMessage}</p>}
        {pageError && <p className="mt-4 rounded-lg bg-red-900/40 px-4 py-2 text-sm text-red-300">{pageError}</p>}
        {config && !config.configured && (
          <p className="mt-4 text-sm text-slate-400">
            Add an <code className="text-slate-200">azure_devops</code> section to <code className="text-slate-200">config.yaml</code> and{" "}
            <code className="text-slate-200">AZURE_DEVOPS_PAT</code> to <code className="text-slate-200">.env</code>, then restart the app — see
            “DevOps Integration (Azure DevOps)” in the README.
          </p>
        )}
      </Card>

      {/* Filters */}
      <Card>
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            From
            <input className={inputClass} onChange={(e) => setDateFrom(e.target.value)} type="date" value={dateFrom} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            To
            <input className={inputClass} onChange={(e) => setDateTo(e.target.value)} type="date" value={dateTo} />
          </label>
          <div className="flex gap-1">
            {[7, 30, 90].map((days) => (
              <button
                className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800"
                key={days}
                onClick={() => setRange(days)}
                type="button"
              >
                {days}d
              </button>
            ))}
          </div>
          {(options?.teams.length ?? 0) > 0 && (
            <label className="flex flex-col gap-1 text-xs text-slate-400">
              Team
              <select
                className={inputClass}
                onChange={(e) => {
                  setTeam(e.target.value);
                  setPerson("");
                }}
                value={team}
              >
                <option value="">All teams</option>
                {options?.teams.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Developer / tester
            <select className={inputClass} onChange={(e) => setPerson(e.target.value)} value={person}>
              <option value="">Everyone</option>
              {people.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.label}
                  {p.role ? ` · ${p.role}` : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Work items
            <select className={inputClass} onChange={(e) => setStatus(e.target.value as "completed" | "all")} value={status}>
              <option value="completed">Finished in range</option>
              <option value="all">Finished + open in range</option>
            </select>
          </label>
          <div className="flex flex-col gap-1 text-xs text-slate-400">
            PR target branch (DORA lead time)
            <BranchSelect
              onChange={onBranchesChange}
              options={branchOptions}
              selected={selectedBranches}
              unknown={unknownBranchPrs}
            />
          </div>
          <button className="rounded-lg px-3 py-2 text-xs text-slate-400 hover:text-white" onClick={clearFilters} type="button">
            Clear filters
          </button>
          <div className="ml-auto flex flex-wrap gap-2">
            <a
              className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800"
              href={`/api/devops/report?format=csv&${query}`}
            >
              ⬇ CSV
            </a>
            <a
              className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800"
              href={`/api/devops/report?format=markdown&${query}`}
            >
              ⬇ Report (Markdown)
            </a>
            <button
              className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800"
              onClick={() => window.print()}
              type="button"
            >
              Print
            </button>
          </div>
        </div>

        {(options?.types.length ?? 0) > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-400">Types:</span>
            {options?.types.map((type) => (
              <button
                className={`rounded-full border px-3 py-1 text-xs transition ${
                  types.includes(type) ? "border-indigo-400 bg-indigo-500/20 text-indigo-200" : "border-slate-700 text-slate-300 hover:bg-slate-800"
                }`}
                key={type}
                onClick={() => setTypes(toggle(types, type))}
                type="button"
              >
                {type}
              </button>
            ))}
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-400">Tags:</span>
          <div className="inline-flex rounded-lg border border-slate-700 p-0.5 text-xs">
            {(["any", "all"] as const).map((mode) => (
              <button
                className={`rounded-md px-2 py-1 ${tagMode === mode ? "bg-slate-700 text-white" : "text-slate-400"}`}
                key={mode}
                onClick={() => setTagMode(mode)}
                title={mode === "any" ? "Items with any selected tag" : "Items with all selected tags"}
                type="button"
              >
                {mode === "any" ? "Any" : "All"}
              </button>
            ))}
          </div>
          <input
            aria-label="Search tags"
            className="w-36 rounded-lg border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-200 focus:outline-none"
            onChange={(e) => setTagSearch(e.target.value)}
            placeholder="Search tags…"
            value={tagSearch}
          />
          {visibleTags.length === 0 && <span className="text-xs text-slate-500">No tags synced yet.</span>}
          {visibleTags.slice(0, 40).map((t) => (
            <button
              className={`rounded-full border px-3 py-1 text-xs transition ${
                tags.includes(t.tag) ? "border-sky-400 bg-sky-500/20 text-sky-200" : "border-slate-700 text-slate-300 hover:bg-slate-800"
              }`}
              key={t.tag}
              onClick={() => setTags(toggle(tags, t.tag))}
              type="button"
            >
              {t.tag} <span className="text-slate-500">{t.count}</span>
            </button>
          ))}
        </div>
      </Card>

      {dataError && <p className="rounded-lg bg-red-900/40 px-4 py-2 text-sm text-red-300">{dataError}</p>}
      {config && !hasData && !loading && (
        <Card>
          <p className="text-sm text-slate-400">
            No Azure DevOps data yet. {config.configured ? "Click “Sync Azure DevOps” to fetch work items and pipeline runs." : "Configure the connection first."}
          </p>
        </Card>
      )}

      <DoraSection dora={dora} loading={loading} scopeLabel={scopeLabel} />
      <StoriesSection data={stories} loading={loading} onSelectPerson={setPerson} />
      <PeopleSection refreshKey={config?.work_items ?? 0} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// People and identities (GitHub login <-> Azure DevOps identity)
// ---------------------------------------------------------------------------

function PeopleSection({ refreshKey }: { refreshKey: number }) {
  const [mapping, setMapping] = useState<PeopleMapping | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getJson<PeopleMapping>("/api/people", controller.signal)
      .then(setMapping)
      .catch(() => undefined);
    return () => controller.abort();
  }, [refreshKey]);

  if (!mapping) return null;
  const unlinked = mapping.unlinked_azure_identities;
  const snippet = unlinked
    .slice(0, 5)
    .map(
      (item) =>
        `    - name: "${item.display_name ?? item.identity}"\n      github: "<their GitHub login>"\n      azure_devops: "${item.identity}"\n      role: developer`,
    )
    .join("\n");

  return (
    <Card>
      <button className="flex w-full items-center justify-between text-left" onClick={() => setOpen(!open)} type="button">
        <div>
          <h3 className="text-lg font-semibold text-white">People &amp; identities</h3>
          <p className="mt-1 text-sm text-slate-400">
            How each person's GitHub login and Azure DevOps identity are linked (config.yaml <code>teams</code>).
            {unlinked.length > 0 && (
              <span className="ml-1 text-amber-300">{unlinked.length} Azure DevOps identit{unlinked.length === 1 ? "y is" : "ies are"} not linked to anyone.</span>
            )}
          </p>
        </div>
        <span className="text-sm text-slate-400">{open ? "Hide ▲" : "Show ▼"}</span>
      </button>

      {open && (
        <div className="mt-4 space-y-6">
          {mapping.people.length === 0 ? (
            <p className="text-sm text-slate-500">
              No teams are configured. Add people under <code>teams</code> in config.yaml to link their GitHub and Azure DevOps names.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="py-2 pr-4">Name</th>
                    <th className="py-2 pr-4">Team</th>
                    <th className="py-2 pr-4">Role</th>
                    <th className="py-2 pr-4">GitHub</th>
                    <th className="py-2 pr-4">Azure DevOps</th>
                    <th className="py-2 pr-4">PRs stored</th>
                    <th className="py-2 pr-4">Work items stored</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800 text-slate-200">
                  {mapping.people.map((person) => (
                    <tr key={person.key}>
                      <td className="py-2 pr-4 font-medium">{person.display_name}</td>
                      <td className="py-2 pr-4 text-slate-400">{person.team}</td>
                      <td className="py-2 pr-4 text-slate-400">{person.role ?? "—"}</td>
                      <td className="py-2 pr-4">{person.github ?? <span className="text-slate-500">none</span>}</td>
                      <td className="py-2 pr-4">
                        {person.azure_devops.length > 0 ? (
                          person.azure_devops.join(", ")
                        ) : (
                          <span className="text-slate-500" title="Matched by display name or GitHub login only">
                            not set
                          </span>
                        )}
                      </td>
                      <td className="py-2 pr-4">{person.pull_requests}</td>
                      <td className={`py-2 pr-4 ${person.work_items === 0 ? "text-amber-300" : ""}`}>{person.work_items}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {unlinked.length > 0 && (
            <div>
              <h4 className="text-sm font-medium text-amber-300">Azure DevOps identities not linked to anyone</h4>
              <p className="mt-1 text-sm text-slate-400">
                Their work items show under the Azure name and are not counted for any GitHub user. Add them to a team in config.yaml, e.g.:
              </p>
              <ul className="mt-2 flex flex-wrap gap-2 text-xs">
                {unlinked.slice(0, 30).map((item) => (
                  <li className="rounded-full border border-slate-700 px-3 py-1 text-slate-300" key={item.identity}>
                    {item.display_name && item.display_name !== item.identity ? `${item.display_name} <${item.identity}>` : item.identity}{" "}
                    <span className="text-slate-500">{item.work_items}</span>
                  </li>
                ))}
              </ul>
              <pre className="mt-3 overflow-x-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-300">{`teams:\n  YourTeam:\n${snippet}`}</pre>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// DORA
// ---------------------------------------------------------------------------

function DoraSection({ dora, loading, scopeLabel }: { dora: DoraResponse | null; loading: boolean; scopeLabel: string }) {
  if (loading && !dora) {
    return (
      <Card>
        <Spinner label="Loading DORA metrics…" />
      </Card>
    );
  }
  if (!dora) return null;

  const cards = [
    {
      key: "deployment_frequency",
      label: "Deployment frequency",
      value: `${dora.deployment_frequency.deployments_per_week ?? 0}/wk`,
      detail: `${dora.deployment_frequency.deployments} deployments on ${dora.deployment_frequency.deployment_days} days`,
      level: dora.deployment_frequency.level,
    },
    {
      key: "lead_time",
      label: "Lead time for changes",
      value: formatHours(dora.lead_time.median_hours),
      detail: `Median, first commit → deployed · ${dora.lead_time.pull_requests} PRs`,
      level: dora.lead_time.level,
    },
    {
      key: "change_failure_rate",
      label: "Change failure rate",
      value: dora.change_failure_rate.rate == null ? "—" : `${dora.change_failure_rate.rate}%`,
      detail:
        dora.method === "incidents"
          ? `${dora.change_failure_rate.failed} of ${dora.change_failure_rate.total} deployments caused incidents`
          : `${dora.change_failure_rate.failed} of ${dora.change_failure_rate.total} deployment runs failed`,
      level: dora.change_failure_rate.level,
    },
    {
      key: "recovery_time",
      label: "Failed deployment recovery time",
      value: formatHours(dora.recovery_time.median_hours),
      detail: `Median over ${dora.recovery_time.samples} ${dora.method === "incidents" ? "incidents" : "failed runs"}`,
      level: dora.recovery_time.level,
    },
  ];

  return (
    <Card>
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-400">DORA metrics · DevLake / DORA 2023 benchmarks</p>
          <h3 className="mt-2 text-xl font-semibold text-white">
            Software delivery performance — {dora.scope.person_label ?? scopeLabel}
          </h3>
          <p className="mt-1 text-sm text-slate-400">
            {dora.scope.kind === "all"
              ? "All production deployments in the date range."
              : "Production deployments that shipped this scope's GitHub pull requests."}{" "}
            Failure and recovery measured from {dora.method === "incidents" ? "incident work items" : "failed deployment runs"}.
          </p>
        </div>
        {loading && <Spinner />}
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4" key={card.key}>
            <div className="flex items-start justify-between gap-2">
              <p className="text-xs uppercase tracking-wide text-slate-400">{card.label}</p>
              <LevelBadge level={card.level} />
            </div>
            <p className="mt-3 text-3xl font-semibold text-white">{card.value}</p>
            <p className="mt-1 text-xs text-slate-400">{card.detail}</p>
            {card.level && (
              <p className="mt-2 text-xs text-slate-500">
                {card.level}: {dora.benchmarks[card.key]?.[card.level]}
              </p>
            )}
          </div>
        ))}
      </div>

      <div className="mt-6 h-72">
        {dora.weekly.every((week) => week.deployments === 0 && week.failed_deployments === 0) ? (
          <div className="flex h-full items-center justify-center text-sm text-slate-500">
            No production deployments in this range. Check <code className="mx-1">deployment_pattern</code> in config.yaml.
          </div>
        ) : (
          <ResponsiveContainer height="100%" width="100%">
            <ComposedChart data={dora.weekly}>
              <CartesianGrid stroke="#334155" strokeDasharray="3 3" />
              <XAxis dataKey="week" stroke="#94a3b8" />
              <YAxis allowDecimals={false} stroke="#94a3b8" yAxisId="count" />
              <YAxis orientation="right" stroke="#94a3b8" yAxisId="hours" />
              <Tooltip />
              <Legend />
              <Bar dataKey="deployments" fill="#22c55e" name="Successful deployments" yAxisId="count" />
              <Bar dataKey="failed_deployments" fill="#f43f5e" name="Failed deployments" yAxisId="count" />
              <Line
                connectNulls
                dataKey="median_lead_time_hours"
                name="Median lead time (h)"
                stroke="#38bdf8"
                strokeWidth={2}
                type="monotone"
                yAxisId="hours"
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
      {dora.pipelines.length > 0 && (
        <p className="mt-3 text-xs text-slate-500">Deployment pipelines in range: {dora.pipelines.join(", ")}</p>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Stories and efficiency
// ---------------------------------------------------------------------------

function StoriesSection({
  data,
  loading,
  onSelectPerson,
}: {
  data: StoriesResponse | null;
  loading: boolean;
  onSelectPerson: (person: string) => void;
}) {
  if (loading && !data) {
    return (
      <Card>
        <Spinner label="Loading stories…" />
      </Card>
    );
  }
  if (!data) return null;
  const { summary, efficiency, stories } = data;
  const maxCompleted = Math.max(1, ...efficiency.map((row) => row.completed));
  const shown = stories.slice(0, 300);

  return (
    <>
      <div className="grid gap-4 md:grid-cols-3 xl:grid-cols-6">
        <StatCard label="Finished" value={`${summary.completed}`} hint="work items closed in range" />
        <StatCard label="Story points" value={`${summary.story_points_completed ?? 0}`} />
        <StatCard label="Throughput" value={`${summary.throughput_per_week ?? 0}/wk`} />
        <StatCard label="Median cycle time" value={formatDays(summary.median_cycle_time_days)} hint="active → closed" />
        <StatCard label="Median lead time" value={formatDays(summary.median_lead_time_days)} hint="created → closed" />
        <StatCard label="Linked PRs" value={`${summary.linked_pr_count}`} hint="GitHub PRs mentioning AB#id" />
      </div>

      <Card>
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold text-white">Efficiency by developer / tester</h3>
          {loading && <Spinner />}
        </div>
        <p className="mt-1 text-sm text-slate-400">
          Work items each person finished in the date range (assignee when closed), plus bugs they reported and GitHub PRs merged.
          Click a row to see that person's DORA metrics.
        </p>
        {efficiency.length === 0 ? (
          <p className="mt-4 text-sm text-slate-500">No finished work items match the filters.</p>
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="py-2 pr-4">Person</th>
                  <th className="py-2 pr-4">Role</th>
                  <th className="py-2 pr-4">Team</th>
                  <th className="py-2 pr-4">Finished</th>
                  <th className="py-2 pr-4">Points</th>
                  <th className="py-2 pr-4">Median cycle</th>
                  <th className="py-2 pr-4">Per week</th>
                  <th className="py-2 pr-4">Share</th>
                  <th className="py-2 pr-4">Bugs reported</th>
                  <th className="py-2 pr-4">PRs merged</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {efficiency.map((row) => (
                  <tr
                    className="cursor-pointer text-slate-200 hover:bg-slate-800/60"
                    key={row.person_key}
                    onClick={() => onSelectPerson(row.person_key)}
                  >
                    <td className="py-2 pr-4 font-medium">{row.person}</td>
                    <td className="py-2 pr-4">
                      {row.role ? (
                        <span className="rounded-full border border-slate-700 px-2 py-0.5 text-xs">{row.role}</span>
                      ) : (
                        <span className="text-slate-500">—</span>
                      )}
                    </td>
                    <td className="py-2 pr-4 text-slate-400">{row.team ?? "—"}</td>
                    <td className="py-2 pr-4">
                      <div className="flex items-center gap-2">
                        <div className="h-2 w-24 rounded-full bg-slate-800">
                          <div className="h-2 rounded-full bg-indigo-500" style={{ width: `${(row.completed / maxCompleted) * 100}%` }} />
                        </div>
                        {row.completed}
                      </div>
                    </td>
                    <td className="py-2 pr-4">{row.story_points ?? 0}</td>
                    <td className="py-2 pr-4">{formatDays(row.median_cycle_time_days)}</td>
                    <td className="py-2 pr-4">{row.throughput_per_week ?? 0}</td>
                    <td className="py-2 pr-4">{row.share_of_completed_pct ?? 0}%</td>
                    <td className="py-2 pr-4">{row.bugs_reported}</td>
                    <td className="py-2 pr-4">{row.prs_merged}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <h3 className="text-lg font-semibold text-white">Work items ({stories.length})</h3>
        {Object.keys(summary.by_type).length > 0 && (
          <p className="mt-1 text-sm text-slate-400">
            {Object.entries(summary.by_type)
              .map(([type, count]) => `${count} ${type}`)
              .join(" · ")}
          </p>
        )}
        {stories.length === 0 ? (
          <p className="mt-4 text-sm text-slate-500">No work items match the filters.</p>
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="py-2 pr-4">ID</th>
                  <th className="py-2 pr-4">Type</th>
                  <th className="py-2 pr-4">Title</th>
                  <th className="py-2 pr-4">State</th>
                  <th className="py-2 pr-4">Assignee</th>
                  <th className="py-2 pr-4">Tags</th>
                  <th className="py-2 pr-4">Points</th>
                  <th className="py-2 pr-4">Closed</th>
                  <th className="py-2 pr-4">Cycle</th>
                  <th className="py-2 pr-4">PRs</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800 text-slate-200">
                {shown.map((story) => (
                  <tr key={story.id}>
                    <td className="py-2 pr-4">
                      {story.url ? (
                        <a className="text-sky-300 hover:underline" href={story.url} rel="noreferrer" target="_blank">
                          {story.id}
                        </a>
                      ) : (
                        story.id
                      )}
                    </td>
                    <td className="py-2 pr-4 text-slate-400">{story.type}</td>
                    <td className="max-w-md py-2 pr-4">{story.title}</td>
                    <td className="py-2 pr-4 text-slate-400">{story.state ?? "—"}</td>
                    <td className="py-2 pr-4">{story.assignee ?? "—"}</td>
                    <td className="py-2 pr-4">
                      <div className="flex flex-wrap gap-1">
                        {story.tags.map((tag) => (
                          <span className="rounded-full border border-slate-700 px-2 py-0.5 text-xs text-slate-300" key={tag}>
                            {tag}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="py-2 pr-4">{story.story_points ?? "—"}</td>
                    <td className="py-2 pr-4 text-slate-400">{story.closed_date?.slice(0, 10) ?? "open"}</td>
                    <td className="py-2 pr-4">{formatDays(story.cycle_time_days)}</td>
                    <td className="py-2 pr-4 text-slate-400">
                      {story.linked_prs.map((pr) => `#${pr.pr_number}`).join(", ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {stories.length > shown.length && (
              <p className="mt-3 text-xs text-slate-500">
                Showing the first {shown.length}; download the CSV for all {stories.length} items.
              </p>
            )}
          </div>
        )}
      </Card>
    </>
  );
}

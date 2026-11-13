import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import BranchSelect, { type BranchOption } from "./BranchSelect";
import DataGrid, { type Column } from "./DataGrid";
import { type DateRange, defaultRange, rangeLabel, rangeToApi } from "./dateRange";
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
  range?: { start: string; end: string; days: number };
  work_items?: number;
  pipeline_runs?: number;
  histories_fetched?: number;
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
  sources?: string[];
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
  runs_total: number;
  runs: PipelineRun[];
}

interface PipelineRun {
  id: number;
  project: string;
  build_number: string | null;
  pipeline: string;
  result: string | null;
  reason: string | null;
  is_production_deployment: boolean;
  repository: string | null;
  source_branch: string | null;
  source_version: string | null;
  requested_for: string | null;
  finish_time: string | null;
  attributed_by: string[];
  url: string | null;
  work_items: { id: number; type: string | null; title: string | null; url: string | null; story_id: number | null; sources: string[] }[];
  stories: number[];
  details_fetched: boolean;
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
  dateRange,
  onDateRangeChange,
}: {
  branchOptions: BranchOption[];
  selectedBranches: string[];
  onBranchesChange: (branches: string[]) => void;
  unknownBranchPrs: number;
  /** Shared with the GitHub pages; also the range fetched by "Sync Azure DevOps". */
  dateRange: DateRange;
  onDateRangeChange: (range: DateRange) => void;
}) {
  const [config, setConfig] = useState<DevOpsConfig | null>(null);
  const [options, setOptions] = useState<FilterOptions | null>(null);
  const [pageError, setPageError] = useState("");

  // Filters
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

  // Filters other than the date range (rolling ranges are resolved at load time).
  const query = useMemo(() => {
    const params = new URLSearchParams({ tag_mode: tagMode, status });
    tags.forEach((tag) => params.append("tags", tag));
    types.forEach((type) => params.append("types", type));
    if (team) params.set("team", team);
    if (person) params.set("person", person);
    selectedBranches.forEach((branch) => params.append("branches", branch));
    return params.toString();
  }, [tagMode, status, tags, types, team, person, selectedBranches]);

  const loadData = useCallback(async () => {
    const fullQuery = `${new URLSearchParams(rangeToApi(dateRange))}&${query}`;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setDataError("");
    try {
      const [storyData, doraData] = await Promise.all([
        getJson<StoriesResponse>(`/api/devops/stories?${fullQuery}`, controller.signal),
        getJson<DoraResponse>(`/api/devops/dora?${fullQuery}`, controller.signal),
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
  }, [query, dateRange]);

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
        body: JSON.stringify(rangeToApi(dateRange)),
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
          `Synced ${run.work_items ?? 0} work item(s) (${run.histories_fetched ?? 0} state histories downloaded) ` +
            `and ${run.pipeline_runs ?? 0} pipeline run(s) ` +
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

  const toggle = (list: string[], value: string) =>
    list.includes(value) ? list.filter((item) => item !== value) : [...list, value];

  const clearFilters = () => {
    setTeam("");
    setPerson("");
    setTypes([]);
    setTags([]);
    setTagMode("any");
    setStatus("completed");
    onDateRangeChange(defaultRange("30d"));
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
          <div className="flex items-center gap-3" data-export="exclude">
            <span className="max-w-xs text-right text-xs text-slate-400">
              Fetches items changed in the selected range:
              <br />
              <span className="text-slate-200">{rangeLabel(dateRange)}</span>
            </span>
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
        {syncMessage && <p className="mt-4 rounded-lg bg-emerald-900/40 px-4 py-2 text-sm text-emerald-300" data-export="exclude">{syncMessage}</p>}
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
          <div className="flex flex-col gap-1 text-xs text-slate-400" title="Change the range with the date picker at the top of the page">
            Date range
            <span className="rounded-lg border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-slate-200">{rangeLabel(dateRange)}</span>
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
          <button className="rounded-lg px-3 py-2 text-xs text-slate-400 hover:text-white" data-export="exclude" onClick={clearFilters} type="button">
            Clear filters
          </button>
          <div className="ml-auto flex flex-wrap gap-2" data-export="exclude">
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
            <DataGrid
              columns={PEOPLE_COLUMNS}
              label="People and identities"
              maxHeight="28rem"
              rowKey={(person) => person.key}
              rows={mapping.people}
            />
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
      <PipelineRunsTable dora={dora} />
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

  return (
    <>
      <div className="grid gap-4 md:grid-cols-3 xl:grid-cols-6">
        <StatCard label="Finished" value={`${summary.completed}`} hint="work items closed in range" />
        <StatCard label="Story points" value={`${summary.story_points_completed ?? 0}`} />
        <StatCard label="Throughput" value={`${summary.throughput_per_week ?? 0}/wk`} />
        <StatCard label="Median cycle time" value={formatDays(summary.median_cycle_time_days)} hint="active → closed" />
        <StatCard label="Median lead time" value={formatDays(summary.median_lead_time_days)} hint="created → closed" />
        <StatCard label="Linked PRs" value={`${summary.linked_pr_count}`} hint="GitHub PRs linked to these work items" />
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
          <div className="mt-4">
            <DataGrid
              columns={efficiencyColumns(maxCompleted)}
              initialSort={{ key: "completed", dir: "desc" }}
              label="Efficiency by developer and tester"
              maxHeight="32rem"
              onRowClick={(row) => onSelectPerson(row.person_key)}
              rowKey={(row) => row.person_key}
              rows={efficiency}
            />
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
          <div className="mt-4">
            <DataGrid
              columns={WORK_ITEM_COLUMNS}
              label="Work items"
              minWidth="64rem"
              rowKey={(story) => story.id}
              rows={stories}
            />
          </div>
        )}
      </Card>
    </>
  );
}

const RESULT_STYLES: Record<string, string> = {
  succeeded: "text-emerald-300",
  partiallysucceeded: "text-amber-300",
  failed: "text-rose-300",
  canceled: "text-slate-400",
};

const stopClick = (event: { stopPropagation: () => void }) => event.stopPropagation();

function PipelineRunsTable({ dora }: { dora: DoraResponse }) {
  const scoped = dora.scope.kind !== "all";
  const detailsMissing = dora.runs.some((run) => !run.details_fetched);
  const columns: Column<PipelineRun>[] = [
    {
      key: "finished",
      header: "Finished",
      value: (run) => run.finish_time ?? "",
      filter: "none",
      render: (run) => <span className="whitespace-nowrap">{run.finish_time ? new Date(`${run.finish_time}Z`).toLocaleString() : "—"}</span>,
    },
    {
      key: "pipeline",
      header: "Pipeline",
      value: (run) => run.pipeline,
      filter: "select",
      render: (run) => (
        <>
          {run.pipeline}
          {run.is_production_deployment && (
            <span className="ml-2 rounded-full border border-indigo-500/40 px-1.5 py-0.5 text-[10px] text-indigo-300">deploy</span>
          )}
        </>
      ),
    },
    {
      key: "type",
      header: "Kind",
      value: (run) => (run.is_production_deployment ? "Production deployment" : "Other run"),
      filter: "select",
      render: (run) => <span className="text-slate-400">{run.is_production_deployment ? "deployment" : "build"}</span>,
    },
    {
      key: "build",
      header: "Build",
      value: (run) => run.build_number ?? String(run.id),
      render: (run) =>
        run.url ? (
          <a className="text-sky-300 hover:underline" href={run.url} onClick={stopClick} rel="noreferrer" target="_blank">
            {run.build_number ?? run.id}
          </a>
        ) : (
          run.build_number ?? run.id
        ),
    },
    {
      key: "result",
      header: "Result",
      value: (run) => run.result ?? "",
      filter: "select",
      render: (run) => <span className={RESULT_STYLES[(run.result ?? "").toLowerCase()] ?? "text-slate-400"}>{run.result ?? "—"}</span>,
    },
    {
      key: "stories",
      header: "Stories",
      value: (run) => run.stories.join(", "),
      filter: "select",
      filterValues: (run) => (run.stories.length ? run.stories.map(String) : ["No story"]),
      title: "Stories behind the run: work items Azure DevOps associates with it, AB# in its commits, and stories of the PRs it built",
      render: (run) =>
        run.work_items.length ? (
          <div className="flex flex-wrap gap-1">
            {run.work_items.map((item) => (
              <a
                className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[11px] text-sky-300 hover:underline"
                href={item.url ?? undefined}
                key={item.id}
                onClick={stopClick}
                rel="noreferrer"
                target="_blank"
                title={`${item.type ?? ""} ${item.title ?? ""}${item.story_id && item.story_id !== item.id ? ` (story #${item.story_id})` : ""} · ${item.sources.join(", ")}`}
              >
                #{item.id}
                {item.story_id && item.story_id !== item.id ? ` → #${item.story_id}` : ""}
              </a>
            ))}
          </div>
        ) : (
          <span className="text-slate-500">—</span>
        ),
    },
    {
      key: "repository",
      header: "Repository / branch",
      value: (run) => `${run.repository ?? ""} ${run.source_branch ?? ""}`,
      render: (run) => (
        <>
          {run.repository ?? "—"}
          {run.source_branch && <span className="block text-slate-500">{run.source_branch.replace(/^refs\/heads\//, "")}</span>}
        </>
      ),
    },
    { key: "requested", header: "Requested for", value: (run) => run.requested_for ?? "", filter: "select" },
  ];
  if (scoped) {
    columns.push({
      key: "why",
      header: "Why it is included",
      value: (run) => run.attributed_by.join("; "),
      render: (run) => <span className="text-slate-400">{run.attributed_by.join("; ") || "—"}</span>,
    });
  }
  return (
    <div className="mt-6 border-t border-slate-800 pt-4">
      <h4 className="text-sm font-semibold text-slate-100">Pipeline runs ({dora.runs_total})</h4>
      <p className="mb-2 text-xs text-slate-500">
        {scoped
          ? "Runs requested for the selection, runs that built or validated their pull requests, and the deployments that shipped them."
          : "Every completed run in the range."}
        {dora.runs_total > dora.runs.length ? ` Showing the latest ${dora.runs.length}.` : ""} Stories come from the work items Azure
        DevOps associates with each run, AB# in the commits it built, and the stories of the PRs it built.
        {detailsMissing ? " Some runs were synced before story lookup was added: sync Azure DevOps again to fill them in." : ""}
      </p>
      <DataGrid
        columns={columns}
        emptyMessage="No pipeline runs for this selection in the date range."
        initialSort={{ key: "finished", dir: "desc" }}
        label="Pipeline runs"
        maxHeight="30rem"
        minWidth="64rem"
        rowKey={(run) => run.id}
        rows={dora.runs}
      />
    </div>
  );
}

type Person = PeopleMapping["people"][number];

const PEOPLE_COLUMNS: Column<Person>[] = [
  { key: "name", header: "Name", value: (p) => p.display_name, className: "font-medium text-slate-100" },
  { key: "team", header: "Team", value: (p) => p.team, filter: "select" },
  { key: "role", header: "Role", value: (p) => p.role ?? "", filter: "select" },
  {
    key: "github",
    header: "GitHub",
    value: (p) => p.github ?? "",
    render: (p) => p.github ?? <span className="text-slate-500">none</span>,
  },
  {
    key: "azure",
    header: "Azure DevOps",
    value: (p) => p.azure_devops.join(", "),
    render: (p) =>
      p.azure_devops.length ? (
        p.azure_devops.join(", ")
      ) : (
        <span className="text-slate-500" title="Matched by display name or GitHub login only">
          not set
        </span>
      ),
  },
  { key: "prs", header: "PRs stored", value: (p) => p.pull_requests, filter: "number" },
  {
    key: "work_items",
    header: "Work items stored",
    value: (p) => p.work_items,
    filter: "number",
    render: (p) => <span className={p.work_items === 0 ? "text-amber-300" : ""}>{p.work_items}</span>,
  },
];

function efficiencyColumns(maxCompleted: number): Column<EfficiencyRow>[] {
  return [
    { key: "person", header: "Person", value: (row) => row.person, className: "font-medium text-slate-100" },
    {
      key: "role",
      header: "Role",
      value: (row) => row.role ?? "",
      filter: "select",
      render: (row) =>
        row.role ? <span className="rounded-full border border-slate-700 px-2 py-0.5 text-xs">{row.role}</span> : <span className="text-slate-500">—</span>,
    },
    { key: "team", header: "Team", value: (row) => row.team ?? "", filter: "select" },
    {
      key: "completed",
      header: "Finished",
      value: (row) => row.completed,
      filter: "number",
      render: (row) => (
        <div className="flex items-center gap-2">
          <div className="h-2 w-24 rounded-full bg-slate-800">
            <div className="h-2 rounded-full bg-indigo-500" style={{ width: `${(row.completed / maxCompleted) * 100}%` }} />
          </div>
          {row.completed}
        </div>
      ),
    },
    { key: "points", header: "Points", value: (row) => row.story_points ?? 0, filter: "number" },
    {
      key: "cycle",
      header: "Median cycle (d)",
      value: (row) => row.median_cycle_time_days,
      filter: "number",
      render: (row) => formatDays(row.median_cycle_time_days),
    },
    { key: "per_week", header: "Per week", value: (row) => row.throughput_per_week ?? 0, filter: "number" },
    {
      key: "share",
      header: "Share %",
      value: (row) => row.share_of_completed_pct ?? 0,
      filter: "number",
      render: (row) => `${row.share_of_completed_pct ?? 0}%`,
    },
    { key: "bugs", header: "Bugs reported", value: (row) => row.bugs_reported, filter: "number" },
    { key: "prs", header: "PRs merged", value: (row) => row.prs_merged, filter: "number" },
  ];
}

const WORK_ITEM_COLUMNS: Column<Story>[] = [
  {
    key: "id",
    header: "ID",
    value: (story) => story.id,
    render: (story) =>
      story.url ? (
        <a className="text-sky-300 hover:underline" href={story.url} rel="noreferrer" target="_blank">
          {story.id}
        </a>
      ) : (
        story.id
      ),
  },
  { key: "type", header: "Type", value: (story) => story.type, filter: "select" },
  { key: "title", header: "Title", value: (story) => story.title, className: "max-w-md" },
  { key: "state", header: "State", value: (story) => story.state ?? "", filter: "select" },
  { key: "assignee", header: "Assignee", value: (story) => story.assignee ?? "", filter: "select" },
  {
    key: "tags",
    header: "Tags",
    value: (story) => story.tags.join(", "),
    filter: "select",
    filterValues: (story) => story.tags,
    render: (story) => (
      <div className="flex flex-wrap gap-1">
        {story.tags.map((tag) => (
          <span className="rounded-full border border-slate-700 px-2 py-0.5 text-xs text-slate-300" key={tag}>
            {tag}
          </span>
        ))}
      </div>
    ),
  },
  { key: "points", header: "Points", value: (story) => story.story_points, filter: "number" },
  {
    key: "closed",
    header: "Closed",
    value: (story) => story.closed_date ?? "",
    render: (story) => <span className="text-slate-400">{story.closed_date?.slice(0, 10) ?? "open"}</span>,
  },
  {
    key: "cycle",
    header: "Cycle (d)",
    value: (story) => story.cycle_time_days,
    filter: "number",
    render: (story) => formatDays(story.cycle_time_days),
  },
  {
    key: "prs",
    header: "PRs",
    value: (story) => story.linked_prs.length,
    filter: "number",
    title: "Linked PRs (hover for how each was linked); filter 0 for work items without code",
    render: (story) =>
      story.linked_prs.length ? (
        <div className="flex flex-wrap gap-1">
          {story.linked_prs.map((pr) => (
            <a
              className="font-mono text-xs text-sky-300 hover:underline"
              href={`https://github.com/${pr.repository}/pull/${pr.pr_number}`}
              key={`${pr.repository}#${pr.pr_number}`}
              rel="noreferrer"
              target="_blank"
              title={`${pr.repository}#${pr.pr_number} by ${pr.author}${pr.sources?.length ? ` · ${pr.sources.join(", ")}` : ""}`}
            >
              #{pr.pr_number}
            </a>
          ))}
        </div>
      ) : (
        <span className="text-slate-500">—</span>
      ),
  },
];

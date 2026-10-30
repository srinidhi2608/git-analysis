import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { type DateRange, rangeLabel, rangeToApi } from "./dateRange";

// ---------------------------------------------------------------------------
// API types (GET /api/stories/lifecycle)
// ---------------------------------------------------------------------------

type StageKind = "wait" | "work" | "released" | "closed" | "other";

interface StageConfig {
  name: string;
  label: string;
  kind: StageKind;
  states: string[];
  order: number;
}

interface TimelineSegment {
  state: string | null;
  stage: string | null;
  label: string | null;
  kind: StageKind;
  start: string;
  end: string | null;
  hours: number | null;
  assignee: string | null;
}

interface LinkedPr {
  repository: string;
  number: number;
  title: string;
  status: "merged" | "open" | "abandoned" | "unknown";
  author: string;
  additions: number;
  deletions: number;
  merged_at: string | null;
  sources: string[];
  url: string;
}

interface StoryRow {
  id: number;
  title: string;
  type: string;
  state: string | null;
  stage: string | null;
  outcome: "released" | "closed_unreleased" | "in_progress";
  team: string | null;
  assignee: string | null;
  tags: string[];
  release_version: string | null;
  story_points: number | null;
  url: string | null;
  created_at: string | null;
  released_at: string | null;
  finished_at: string | null;
  lead_time_hours: number | null;
  cycle_time_hours: number | null;
  age_hours: number | null;
  stage_hours: Record<string, number>;
  work_hours: number | null;
  wait_hours: number | null;
  flow_efficiency: number | null;
  rework_count: number;
  qa_bounces: number;
  reopened: number;
  repeated_stages: string[];
  skipped_stages: string[];
  pr_count: number;
  prs_merged: number;
  prs_open: number;
  prs_abandoned: number;
  lines_added: number;
  lines_deleted: number;
  code_time_hours: number | null;
  code_share: number | null;
  developers: { key: string; name: string }[];
  testers: { key: string; name: string }[];
  bugs_linked: number;
  escaped_defects: number;
  flags: string[];
  timeline: TimelineSegment[];
  pull_requests: LinkedPr[];
}

interface Summary {
  stories: number;
  released: number;
  closed_unreleased: number;
  in_progress: number;
  median_lead_time_days: number | null;
  median_cycle_time_days: number | null;
  median_flow_efficiency: number | null;
  median_code_share: number | null;
  first_time_right_pct: number | null;
  qa_bounce_rate_pct: number | null;
  reopen_rate_pct: number | null;
  defects_per_kloc: number | null;
  escaped_defects: number;
  median_stage_hours: Record<string, number | null>;
  flags: Record<string, number>;
  releases: Record<string, number>;
  unmapped_states: string[];
}

interface LifecycleResponse {
  config: { stages: StageConfig[]; testing_enabled: boolean; release_tag_pattern: string };
  summary: Summary;
  stories: StoryRow[];
}

interface FilterOptions {
  people: { key: string; label: string; role: string | null; team: string | null }[];
  teams: string[];
  tags: { tag: string; count: number }[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const FLAG_LABELS: Record<string, string> = {
  qa_bounce: "Bounced back from QA",
  reopened: "Reopened after release/close",
  skipped_stages: "Skipped a testing stage",
  closed_directly: "Closed without development",
  closed_without_code: "Finished without linked PRs",
  open_pr_at_finish: "Open PR when finished",
  abandoned_pr: "Abandoned PR",
  released_without_release_tag: "Released without a Release tag",
  release_tag_not_released: "Release tag but not released",
  closed_unreleased_with_code: "Closed unreleased with merged code",
  unmapped_states: "State not in the stage mapping",
  no_history: "No state history yet",
};

const KIND_COLORS: Record<StageKind, string> = {
  wait: "bg-slate-600",
  work: "bg-sky-500",
  released: "bg-emerald-500",
  closed: "bg-slate-400",
  other: "bg-rose-500",
};
// Distinct colors for the testing work stages, by position after development.
const TEST_COLORS = ["bg-amber-500", "bg-violet-500", "bg-fuchsia-500", "bg-orange-500"];

const OUTCOME_STYLES: Record<StoryRow["outcome"], string> = {
  released: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  closed_unreleased: "border-slate-500/40 bg-slate-500/10 text-slate-300",
  in_progress: "border-sky-500/40 bg-sky-500/10 text-sky-300",
};
const OUTCOME_LABELS: Record<StoryRow["outcome"], string> = {
  released: "Released",
  closed_unreleased: "Closed (not released)",
  in_progress: "In progress",
};

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

function days(hours: number | null | undefined): string {
  if (hours == null) return "—";
  return hours < 24 ? `${hours.toFixed(1)} h` : `${(hours / 24).toFixed(1)} d`;
}

function pct(value: number | null | undefined, ratio = false): string {
  if (value == null) return "—";
  return `${(ratio ? value * 100 : value).toFixed(0)}%`;
}

function shortDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "2-digit" }) : "—";
}

function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl shadow-black/20 ${className}`}>{children}</div>;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
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

export default function StoryQualityPage({ dateRange }: { dateRange: DateRange }) {
  const [team, setTeam] = useState("");
  const [person, setPerson] = useState("");
  const [tag, setTag] = useState("");
  const [status, setStatus] = useState<"completed" | "all">("completed");
  const [flag, setFlag] = useState("");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);

  const [options, setOptions] = useState<FilterOptions | null>(null);
  const [data, setData] = useState<LifecycleResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    getJson<FilterOptions>("/api/devops/filters")
      .then(setOptions)
      .catch(() => setOptions(null));
    return () => abortRef.current?.abort();
  }, []);

  const filterQuery = useMemo(() => {
    const params = new URLSearchParams({ status });
    if (team) params.set("team", team);
    if (person) params.set("person", person);
    if (tag) params.append("tags", tag);
    return params.toString();
  }, [status, team, person, tag]);

  // Rolling ranges are resolved when the request is made.
  const fullQuery = useCallback(() => `${new URLSearchParams(rangeToApi(dateRange))}&${filterQuery}`, [dateRange, filterQuery]);

  const load = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError("");
    try {
      setData(await getJson<LifecycleResponse>(`/api/stories/lifecycle?${fullQuery()}`, controller.signal));
    } catch (err) {
      if (isAbortError(err)) return;
      setData(null);
      setError((err as Error).message);
    } finally {
      if (abortRef.current === controller) setLoading(false);
    }
  }, [fullQuery]);

  useEffect(() => {
    const timer = setTimeout(load, 250);
    return () => clearTimeout(timer);
  }, [load]);

  const stages = useMemo(() => data?.config.stages ?? [], [data]);
  const stageColor = useMemo(() => {
    const colors: Record<string, string> = {};
    const devOrder = stages.find((s) => s.kind === "work")?.order ?? -1;
    let testIndex = 0;
    for (const stage of stages) {
      if (stage.kind === "work" && stage.order > devOrder) colors[stage.name] = TEST_COLORS[testIndex++ % TEST_COLORS.length];
      else colors[stage.name] = KIND_COLORS[stage.kind];
    }
    return colors;
  }, [stages]);

  const stories = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (data?.stories ?? []).filter(
      (row) =>
        (!flag || row.flags.includes(flag)) &&
        (!term || row.title.toLowerCase().includes(term) || String(row.id).includes(term)),
    );
  }, [data, flag, search]);

  const people = (options?.people ?? []).filter((p) => !team || p.team === team);
  const summary = data?.summary;
  const csvHref = (report: string) => `/api/reports/${report}.csv?${fullQuery()}`;

  return (
    <div className="space-y-6">
      <Card>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-400">Azure Boards lifecycle + GitHub</p>
            <h2 className="mt-2 text-2xl font-semibold text-white">Story Quality</h2>
            <p className="mt-2 max-w-3xl text-sm text-slate-300">
              Every user story from creation to release: time in each stage, the pull requests behind it, who built and tested it,
              rework, and integrity checks. Stages come from <code className="text-slate-200">lifecycle</code> in config.yaml.
            </p>
            <p className="mt-2 text-xs text-slate-500">Range: {rangeLabel(dateRange)} (change it at the top of the page)</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {[
              ["story-lifecycle", "Stories CSV"],
              ["story-transitions", "State changes CSV"],
              ["story-pull-requests", "Story PRs CSV"],
            ].map(([report, label]) => (
              <a
                className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 transition hover:bg-slate-800"
                download
                href={csvHref(report)}
                key={report}
              >
                ⤓ {label}
              </a>
            ))}
          </div>
        </div>

        <div className="mt-5 flex flex-wrap items-end gap-4 border-t border-slate-800 pt-4">
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Stories
            <select className={inputClass} onChange={(e) => setStatus(e.target.value as "completed" | "all")} value={status}>
              <option value="completed">Released or closed in range</option>
              <option value="all">Also in progress</option>
            </select>
          </label>
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
            Tag
            <select className={inputClass} onChange={(e) => setTag(e.target.value)} value={tag}>
              <option value="">Any tag</option>
              {options?.tags.map((t) => (
                <option key={t.tag} value={t.tag}>
                  {t.tag} ({t.count})
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Flag
            <select className={inputClass} onChange={(e) => setFlag(e.target.value)} value={flag}>
              <option value="">Any</option>
              {Object.entries(summary?.flags ?? {}).map(([name, count]) => (
                <option key={name} value={name}>
                  {FLAG_LABELS[name] ?? name} ({count})
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Search
            <input className={inputClass} onChange={(e) => setSearch(e.target.value)} placeholder="Title or ID" value={search} />
          </label>
        </div>
        {error && <p className="mt-4 rounded-lg bg-red-900/40 px-4 py-2 text-sm text-red-300">{error}</p>}
        {summary && summary.unmapped_states.length > 0 && (
          <p className="mt-4 rounded-lg bg-amber-900/30 px-4 py-2 text-sm text-amber-200">
            These board states are not in <code>lifecycle.stages</code>, so their time is not counted in any stage:{" "}
            {summary.unmapped_states.join(", ")}. Add them to config.yaml.
          </p>
        )}
      </Card>

      {loading && !data ? (
        <Card>
          <p className="text-sm text-slate-400">Loading stories…</p>
        </Card>
      ) : summary ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="Stories"
              value={String(summary.stories)}
              hint={`${summary.released} released · ${summary.closed_unreleased} closed unreleased · ${summary.in_progress} in progress`}
            />
            <Stat label="First-time-right" value={pct(summary.first_time_right_pct)} hint="Released with no move back to an earlier stage" />
            <Stat label="QA bounce rate" value={pct(summary.qa_bounce_rate_pct)} hint="Tested stories sent back to development" />
            <Stat label="Reopen rate" value={pct(summary.reopen_rate_pct)} hint="Moved back after release or close" />
            <Stat label="Lead time (median)" value={summary.median_lead_time_days == null ? "—" : `${summary.median_lead_time_days} d`} hint="Created → released" />
            <Stat label="Cycle time (median)" value={summary.median_cycle_time_days == null ? "—" : `${summary.median_cycle_time_days} d`} hint="Development start → released" />
            <Stat label="Flow efficiency (median)" value={pct(summary.median_flow_efficiency, true)} hint="Active time ÷ (active + waiting)" />
            <Stat label="Code share (median)" value={pct(summary.median_code_share, true)} hint="First commit → last merge ÷ cycle time" />
          </div>

          <Card>
            <h3 className="text-lg font-semibold text-white">Median time per stage</h3>
            <p className="mt-1 text-xs text-slate-500">Over the stories that entered each stage. Hover a story's bar below for its own timeline.</p>
            <div className="mt-4 grid gap-2">
              {stages
                .filter((stage) => stage.kind !== "released" && stage.kind !== "closed")
                .map((stage) => {
                  const hours = summary.median_stage_hours[stage.name];
                  const max = Math.max(1, ...Object.values(summary.median_stage_hours).map((v) => v ?? 0));
                  return (
                    <div className="grid grid-cols-[9rem_1fr_4.5rem] items-center gap-3 text-sm" key={stage.name}>
                      <span className="truncate text-slate-300" title={stage.states.join(", ")}>
                        {stage.label}
                      </span>
                      <div className="h-3 rounded bg-slate-800">
                        <div className={`h-3 rounded ${stageColor[stage.name]}`} style={{ width: `${((hours ?? 0) / max) * 100}%` }} />
                      </div>
                      <span className="text-right font-mono text-xs tabular-nums text-slate-300">{days(hours)}</span>
                    </div>
                  );
                })}
            </div>
            <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-400">
              <span>Bugs per 1,000 changed lines: {summary.defects_per_kloc ?? "—"}</span>
              <span>Escaped defects: {summary.escaped_defects}</span>
              {Object.keys(summary.releases).length > 0 && (
                <span>
                  Releases:{" "}
                  {Object.entries(summary.releases)
                    .map(([version, count]) => `${version} (${count})`)
                    .join(", ")}
                </span>
              )}
              <span>Testing data: {data?.config.testing_enabled ? "enabled" : "not integrated yet (placeholder columns in CSV)"}</span>
            </div>
          </Card>

          <Card className="overflow-hidden p-0">
            <div className="flex items-center justify-between px-6 pt-5">
              <h3 className="text-lg font-semibold text-white">Stories ({stories.length})</h3>
              {loading && <span className="text-xs text-slate-500">Refreshing…</span>}
            </div>
            {stories.length === 0 ? (
              <p className="px-6 py-6 text-sm text-slate-400">
                No stories match. Stories need their state history: run “Sync Azure DevOps” on the DevOps Integration tab, or widen the date range.
              </p>
            ) : (
              <div className="mt-3 max-h-[42rem] overflow-auto">
                <table className="w-full min-w-[72rem] text-left text-sm">
                  <thead className="sticky top-0 z-10 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
                    <tr>
                      <th className="px-4 py-2 font-medium">Story</th>
                      <th className="px-4 py-2 font-medium">Outcome</th>
                      <th className="w-[22rem] px-4 py-2 font-medium">Timeline</th>
                      <th className="px-4 py-2 font-medium">Lead</th>
                      <th className="px-4 py-2 font-medium">PRs</th>
                      <th className="px-4 py-2 font-medium">Lines</th>
                      <th className="px-4 py-2 font-medium">Rework</th>
                      <th className="px-4 py-2 font-medium">Flags</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800 text-slate-300">
                    {stories.map((row) => (
                      <Fragment key={row.id}>
                        <tr
                          className="cursor-pointer hover:bg-slate-800/40"
                          onClick={() => setExpanded(expanded === row.id ? null : row.id)}
                        >
                          <td className="min-w-[18rem] px-4 py-3">
                            <span className="font-mono text-xs text-slate-500">#{row.id}</span>{" "}
                            <span className="text-slate-100">{row.title}</span>
                            <span className="mt-0.5 block text-xs text-slate-500">
                              {row.release_version ? `Release ${row.release_version} · ` : ""}
                              {row.story_points != null ? `${row.story_points} pts · ` : ""}
                              {row.state}
                            </span>
                          </td>
                          <td className="px-4 py-3">
                            <span className={`whitespace-nowrap rounded-full border px-2 py-0.5 text-xs ${OUTCOME_STYLES[row.outcome]}`}>
                              {OUTCOME_LABELS[row.outcome]}
                            </span>
                          </td>
                          <td className="px-4 py-3">
                            <TimelineBar row={row} stageColor={stageColor} />
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 font-mono text-xs tabular-nums">
                            {row.outcome === "in_progress" ? `${days(row.age_hours)} old` : days(row.lead_time_hours)}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-xs">
                            {row.prs_merged} merged
                            {row.prs_open ? <span className="text-amber-300"> · {row.prs_open} open</span> : null}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 font-mono text-xs tabular-nums">
                            +{row.lines_added} / −{row.lines_deleted}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-xs">
                            {row.rework_count ? <span className="text-amber-300">{row.rework_count}×</span> : "—"}
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex flex-wrap gap-1">
                              {row.flags.map((name) => (
                                <span className="rounded border border-amber-500/30 px-1.5 py-0.5 text-[11px] text-amber-200" key={name} title={FLAG_LABELS[name]}>
                                  {FLAG_LABELS[name] ?? name}
                                </span>
                              ))}
                            </div>
                          </td>
                        </tr>
                        {expanded === row.id && (
                          <tr className="bg-slate-950/60">
                            <td className="px-4 py-4" colSpan={8}>
                              <StoryDetail row={row} stageColor={stageColor} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      ) : null}
    </div>
  );
}

function TimelineBar({ row, stageColor }: { row: StoryRow; stageColor: Record<string, string> }) {
  const segments = row.timeline.filter((segment) => (segment.hours ?? 0) > 0);
  const total = segments.reduce((sum, segment) => sum + (segment.hours ?? 0), 0);
  if (!total) return <span className="text-xs text-slate-500">No history</span>;
  return (
    <div className="flex h-3 w-full overflow-hidden rounded" role="img" aria-label={`Timeline of story ${row.id}`}>
      {segments.map((segment, index) => (
        <span
          className={segment.stage ? stageColor[segment.stage] : KIND_COLORS.other}
          key={index}
          style={{ width: `${((segment.hours ?? 0) / total) * 100}%` }}
          title={`${segment.label ?? segment.state}: ${days(segment.hours)}${segment.assignee ? ` · ${segment.assignee}` : ""}`}
        />
      ))}
    </div>
  );
}

function StoryDetail({ row, stageColor }: { row: StoryRow; stageColor: Record<string, string> }) {
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div className="min-w-0">
        <h4 className="text-sm font-semibold text-slate-100">State history</h4>
        <ol className="mt-2 space-y-1 text-xs">
          {row.timeline.map((segment, index) => (
            <li className="flex items-center gap-2" key={index}>
              <span className={`h-2.5 w-2.5 shrink-0 rounded-sm ${segment.stage ? stageColor[segment.stage] : KIND_COLORS.other}`} />
              <span className="w-36 shrink-0 text-slate-200">{segment.state}</span>
              <span className="w-24 shrink-0 text-slate-500">{shortDate(segment.start)}</span>
              <span className="w-16 shrink-0 font-mono tabular-nums text-slate-300">{days(segment.hours)}</span>
              <span className="truncate text-slate-500">{segment.assignee}</span>
            </li>
          ))}
        </ol>
        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
          {[
            ["Active / waiting", `${days(row.work_hours)} / ${days(row.wait_hours)}`],
            ["Flow efficiency", pct(row.flow_efficiency, true)],
            ["Code time", days(row.code_time_hours)],
            ["Code share", pct(row.code_share, true)],
            ["QA bounces", String(row.qa_bounces)],
            ["Reopened", String(row.reopened)],
            ["Repeated stages", row.repeated_stages.join(", ") || "—"],
            ["Skipped stages", row.skipped_stages.join(", ") || "—"],
            ["Bugs (escaped)", `${row.bugs_linked} (${row.escaped_defects})`],
          ].map(([label, value]) => (
            <div key={label}>
              <dt className="text-slate-500">{label}</dt>
              <dd className="text-slate-200">{value}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-xs text-slate-400">
          Developers: {row.developers.map((p) => p.name).join(", ") || "—"} · Testers: {row.testers.map((p) => p.name).join(", ") || "—"}
        </p>
        {row.url && (
          <a className="mt-2 inline-block text-xs text-sky-300 hover:underline" href={row.url} rel="noreferrer" target="_blank">
            Open in Azure Boards ↗
          </a>
        )}
      </div>
      <div className="min-w-0">
        <h4 className="text-sm font-semibold text-slate-100">Pull requests ({row.pull_requests.length})</h4>
        {row.pull_requests.length === 0 ? (
          <p className="mt-2 text-xs text-slate-500">
            No linked PRs. Link them with AB#{row.id} in the PR title or description, or from the story in Azure Boards.
          </p>
        ) : (
          <ul className="mt-2 space-y-2 text-xs">
            {row.pull_requests.map((pr) => (
              <li className="rounded-lg border border-slate-800 px-3 py-2" key={pr.url}>
                <a className="text-sky-300 hover:underline" href={pr.url} rel="noreferrer" target="_blank">
                  {pr.repository}#{pr.number}
                </a>{" "}
                <span className="text-slate-200">{pr.title}</span>
                <span className="mt-1 block text-slate-500">
                  {pr.status} · {pr.author} · +{pr.additions}/−{pr.deletions} · linked by {pr.sources.join(", ")}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import DataGrid, { type Column } from "./DataGrid";
import { type DateRange, rangeToApi } from "./dateRange";

// ---------------------------------------------------------------------------
// API types (GET /api/pull-requests)
// ---------------------------------------------------------------------------

type Phase = "coding" | "pickup" | "review" | "merge" | "deploy";

interface PhaseSegment {
  phase: Phase;
  label: string;
  start: string;
  end: string | null;
  hours: number | null;
  ongoing: boolean;
}

interface WorkItemLink {
  id: number;
  type: string | null;
  title: string | null;
  state: string | null;
  url: string | null;
  story_id: number | null;
  sources: string[];
}

interface PullRequestRow {
  id: number;
  repository: string;
  number: number;
  title: string;
  url: string;
  author: string;
  team: string | null;
  status: "merged" | "open" | "abandoned" | "unknown";
  base_branch: string | null;
  head_branch: string | null;
  first_commit_at: string | null;
  created_at: string | null;
  first_review_at: string | null;
  approved_at: string | null;
  merged_at: string | null;
  deployed_at: string | null;
  deployment: { pipeline: string; build_number: string | null; url: string | null } | null;
  cycle_hours: number | null;
  lead_to_production_hours: number | null;
  age_hours: number | null;
  additions: number;
  deletions: number;
  reviewers: number;
  review_comments: number;
  changes_requested: number;
  stories: number[];
  work_items: WorkItemLink[];
  unresolved_work_item_refs: number[];
  flags: string[];
  timeline: PhaseSegment[];
}

interface PullRequestSummary {
  pull_requests: number;
  merged: number;
  open: number;
  abandoned: number;
  without_story: number;
  without_story_pct: number | null;
  story_not_synced: number;
  unreviewed_merges: number;
  median_hours: Record<Phase, number | null>;
  median_cycle_hours: number | null;
  median_lead_to_production_hours: number | null;
  deployments_configured: boolean;
}

interface PullRequestResponse {
  summary: PullRequestSummary;
  pull_requests: PullRequestRow[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const PHASE_COLORS: Record<Phase, string> = {
  coding: "bg-sky-500",
  pickup: "bg-slate-500",
  review: "bg-amber-500",
  merge: "bg-violet-500",
  deploy: "bg-emerald-500",
};
const PHASE_LABELS: Record<Phase, string> = {
  coding: "Coding",
  pickup: "Waiting for review",
  review: "In review",
  merge: "Waiting to merge",
  deploy: "Waiting to deploy",
};
const FLAG_LABELS: Record<string, string> = {
  no_story: "No story linked",
  story_not_synced: "Linked work item not synced",
  unreviewed_merge: "Merged without review",
  large_pr: "Large PR",
  changes_requested: "Changes requested",
  stale_open: "Open for over a week",
  abandoned: "Closed without merging",
  not_deployed: "Merged but not deployed",
  unknown_state: "State unknown (re-sync)",
};
const STATUS_STYLES: Record<PullRequestRow["status"], string> = {
  merged: "border-emerald-500/40 text-emerald-300",
  open: "border-sky-500/40 text-sky-300",
  abandoned: "border-slate-500/40 text-slate-300",
  unknown: "border-slate-700 text-slate-400",
};

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

function duration(hours: number | null | undefined): string {
  if (hours == null) return "—";
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  return hours < 48 ? `${hours.toFixed(1)} h` : `${(hours / 24).toFixed(1)} d`;
}

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
}

const stop = (event: MouseEvent) => event.stopPropagation();

function PhaseBar({ row }: { row: PullRequestRow }) {
  const segments = row.timeline.filter((segment) => (segment.hours ?? 0) > 0);
  const total = segments.reduce((sum, segment) => sum + (segment.hours ?? 0), 0);
  if (!total) return <span className="text-xs text-slate-500">—</span>;
  return (
    <div aria-label={`Timeline of ${row.repository}#${row.number}`} className="flex h-3 w-full min-w-[10rem] overflow-hidden rounded" role="img">
      {segments.map((segment) => (
        <span
          className={`${PHASE_COLORS[segment.phase]} ${segment.ongoing ? "opacity-60" : ""}`}
          key={segment.phase}
          style={{ width: `${((segment.hours ?? 0) / total) * 100}%` }}
          title={`${segment.label}: ${duration(segment.hours)}${segment.ongoing ? " (ongoing)" : ""}`}
        />
      ))}
    </div>
  );
}

function storyLabel(row: PullRequestRow): string[] {
  if (row.stories.length) return row.stories.map(String);
  if (row.unresolved_work_item_refs.length) return ["Not synced"];
  return ["No story"];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function PullRequestTimeline({
  dateRange,
  team,
  branches,
}: {
  dateRange: DateRange;
  team: string;
  branches: string[];
}) {
  const [data, setData] = useState<PullRequestResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const query = useCallback(() => {
    const params = new URLSearchParams(rangeToApi(dateRange));
    if (team) params.set("team", team);
    branches.forEach((branch) => params.append("branches", branch));
    return params.toString();
  }, [dateRange, team, branches]);

  useEffect(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const timer = setTimeout(async () => {
      setLoading(true);
      setError("");
      try {
        const res = await fetch(`/api/pull-requests?${query()}`, { signal: controller.signal });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        setData((await res.json()) as PullRequestResponse);
      } catch (err) {
        if (isAbortError(err)) return;
        setError(`Failed to load pull requests: ${(err as Error).message}`);
      } finally {
        if (abortRef.current === controller) setLoading(false);
      }
    }, 200);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query]);

  const phases = useMemo(
    () => (Object.keys(PHASE_LABELS) as Phase[]).filter((phase) => phase !== "deploy" || data?.summary.deployments_configured),
    [data],
  );

  const columns = useMemo<Column<PullRequestRow>[]>(
    () => [
      {
        key: "pr",
        header: "Pull request",
        value: (row) => `${row.repository}#${row.number} ${row.title}`,
        className: "min-w-[18rem]",
        render: (row) => (
          <div>
            <a className="font-mono text-xs text-sky-300 hover:underline" href={row.url} onClick={stop} rel="noreferrer" target="_blank">
              {row.repository}#{row.number}
            </a>
            <span className="block text-slate-100">{row.title}</span>
            {row.base_branch && (
              <span className="text-xs text-slate-500">
                {row.head_branch ? `${row.head_branch} ` : ""}→ {row.base_branch}
              </span>
            )}
          </div>
        ),
      },
      { key: "author", header: "Author", value: (row) => row.author, filter: "select" },
      {
        key: "status",
        header: "Status",
        value: (row) => row.status,
        filter: "select",
        render: (row) => <span className={`rounded-full border px-2 py-0.5 text-xs ${STATUS_STYLES[row.status]}`}>{row.status}</span>,
      },
      {
        key: "stories",
        header: "Stories",
        value: (row) => (row.stories.length ? row.stories.join(", ") : row.unresolved_work_item_refs.length ? "Not synced" : ""),
        filter: "select",
        filterValues: storyLabel,
        title: "Stories linked to the PR (directly or through a child task/bug). Filter “No story” to find unlinked PRs.",
        render: (row) =>
          row.stories.length ? (
            <div className="flex flex-wrap gap-1">
              {row.stories.map((id) => {
                const item = row.work_items.find((link) => link.story_id === id);
                return (
                  <span className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-xs" key={id} title={item?.sources.join(", ")}>
                    #{id}
                  </span>
                );
              })}
            </div>
          ) : row.unresolved_work_item_refs.length ? (
            <span className="text-xs text-amber-300" title="Mentioned with AB#, but not stored: sync Azure DevOps">
              AB#{row.unresolved_work_item_refs.join(", AB#")} (not synced)
            </span>
          ) : (
            <span className="rounded border border-rose-500/40 px-1.5 py-0.5 text-xs text-rose-300">No story</span>
          ),
      },
      { key: "timeline", header: "Timeline", value: (row) => row.cycle_hours ?? row.age_hours, filter: "none", className: "w-[16rem]", render: (row) => <PhaseBar row={row} /> },
      {
        key: "cycle",
        header: "Cycle (h)",
        value: (row) => row.cycle_hours ?? row.age_hours,
        filter: "number",
        title: "First commit → merge (age for open PRs), in hours",
        render: (row) => (
          <span className="whitespace-nowrap font-mono text-xs tabular-nums">
            {row.status === "open" ? `${duration(row.age_hours)} open` : duration(row.cycle_hours)}
          </span>
        ),
      },
      {
        key: "merged",
        header: "Merged",
        value: (row) => row.merged_at ?? "",
        filter: "none",
        render: (row) => <span className="whitespace-nowrap text-xs">{when(row.merged_at)}</span>,
      },
      {
        key: "lines",
        header: "Lines",
        value: (row) => row.additions + row.deletions,
        filter: "number",
        render: (row) => (
          <span className="whitespace-nowrap font-mono text-xs tabular-nums">
            +{row.additions} / −{row.deletions}
          </span>
        ),
      },
      { key: "reviewers", header: "Reviewers", value: (row) => row.reviewers, filter: "number", className: "text-right" },
      {
        key: "flags",
        header: "Flags",
        value: (row) => row.flags.map((flag) => FLAG_LABELS[flag] ?? flag).join(", "),
        filter: "select",
        filterValues: (row) => row.flags.map((flag) => FLAG_LABELS[flag] ?? flag),
        render: (row) => (
          <div className="flex flex-wrap gap-1">
            {row.flags.map((flag) => (
              <span className="rounded border border-amber-500/30 px-1.5 py-0.5 text-[11px] text-amber-200" key={flag}>
                {FLAG_LABELS[flag] ?? flag}
              </span>
            ))}
          </div>
        ),
      },
    ],
    [],
  );

  const summary = data?.summary;
  const maxMedian = Math.max(1, ...phases.map((phase) => summary?.median_hours[phase] ?? 0));

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl shadow-black/20">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-400">GitHub + Azure Boards + Azure Pipelines</p>
          <h2 className="mt-2 text-2xl font-semibold text-white">Pull request timeline</h2>
          <p className="mt-2 max-w-3xl text-sm text-slate-300">
            Every PR created or merged in the range: how long it spent coding, waiting for review, in review, waiting to merge
            {summary?.deployments_configured ? " and waiting to deploy" : ""}, and the stories it belongs to. Click a PR for details.
          </p>
        </div>
        <a
          className="shrink-0 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200 transition hover:bg-slate-800"
          download
          href={`/api/reports/pull-requests.csv?${query()}`}
        >
          ⤓ Pull requests CSV
        </a>
      </div>

      {error && <p className="mt-4 rounded-lg bg-red-900/40 px-4 py-2 text-sm text-red-300">{error}</p>}
      {loading && !data && <p className="mt-4 text-sm text-slate-400">Loading pull requests…</p>}

      {summary && (
        <>
          <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Tile label="Pull requests" value={String(summary.pull_requests)} hint={`${summary.merged} merged · ${summary.open} open · ${summary.abandoned} closed unmerged`} />
            <Tile
              label="Without a story"
              tone={summary.without_story ? "warn" : undefined}
              value={`${summary.without_story}${summary.without_story_pct != null ? ` (${summary.without_story_pct}%)` : ""}`}
              hint={summary.story_not_synced ? `${summary.story_not_synced} more mention a work item that isn't synced` : "Filter Stories → “No story” below"}
            />
            <Tile label="Merged without review" tone={summary.unreviewed_merges ? "warn" : undefined} value={String(summary.unreviewed_merges)} />
            <Tile
              label="Median cycle"
              value={duration(summary.median_cycle_hours)}
              hint={summary.deployments_configured ? `To production: ${duration(summary.median_lead_to_production_hours)}` : "First commit → merge"}
            />
          </div>
          <div className="mt-5 grid gap-2">
            <p className="text-xs uppercase tracking-wide text-slate-400">Median time per phase (merged PRs)</p>
            {phases.map((phase) => (
              <div className="grid grid-cols-[10rem_1fr_4.5rem] items-center gap-3 text-sm" key={phase}>
                <span className="flex items-center gap-2 text-slate-300">
                  <span className={`h-2.5 w-2.5 rounded-sm ${PHASE_COLORS[phase]}`} />
                  {PHASE_LABELS[phase]}
                </span>
                <div className="h-2.5 rounded bg-slate-800">
                  <div className={`h-2.5 rounded ${PHASE_COLORS[phase]}`} style={{ width: `${((summary.median_hours[phase] ?? 0) / maxMedian) * 100}%` }} />
                </div>
                <span className="text-right font-mono text-xs tabular-nums text-slate-300">{duration(summary.median_hours[phase])}</span>
              </div>
            ))}
          </div>
          <div className="mt-5">
            <DataGrid
              columns={columns}
              emptyMessage="No pull requests in this range. Sync from GitHub or widen the date range."
              initialSort={{ key: "merged", dir: "desc" }}
              label="Pull requests"
              minWidth="76rem"
              renderExpanded={(row) => <PullRequestDetail row={row} />}
              rowKey={(row) => row.id}
              rows={data.pull_requests}
            />
          </div>
        </>
      )}
    </div>
  );
}

function Tile({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "warn" }) {
  return (
    <div className={`rounded-xl border p-4 ${tone === "warn" ? "border-amber-500/30 bg-amber-500/5" : "border-slate-800 bg-slate-900"}`}>
      <p className="text-xs uppercase tracking-wide text-slate-400">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-slate-50">{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

function PullRequestDetail({ row }: { row: PullRequestRow }) {
  const milestones: [string, string | null][] = [
    ["First commit", row.first_commit_at],
    ["Opened", row.created_at],
    ["First review", row.first_review_at],
    ["Approved", row.approved_at],
    ["Merged", row.merged_at],
    ["Deployed to production", row.deployed_at],
  ];
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="min-w-0">
        <h4 className="text-sm font-semibold text-slate-100">Phases</h4>
        <ul className="mt-2 space-y-1 text-xs">
          {row.timeline.map((segment) => (
            <li className="flex items-center gap-2" key={segment.phase}>
              <span className={`h-2.5 w-2.5 shrink-0 rounded-sm ${PHASE_COLORS[segment.phase]}`} />
              <span className="w-36 shrink-0 text-slate-200">{segment.label}</span>
              <span className="font-mono tabular-nums text-slate-300">{duration(segment.hours)}</span>
              {segment.ongoing && <span className="text-slate-500">(ongoing)</span>}
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-slate-400">
          {row.review_comments} review comments · {row.changes_requested} change requests · {row.reviewers} reviewers
        </p>
      </div>
      <div className="min-w-0">
        <h4 className="text-sm font-semibold text-slate-100">Milestones</h4>
        <dl className="mt-2 grid grid-cols-[9rem_1fr] gap-y-1 text-xs">
          {milestones.map(([label, value]) => (
            <div className="contents" key={label}>
              <dt className="text-slate-500">{label}</dt>
              <dd className="text-slate-200">{when(value)}</dd>
            </div>
          ))}
        </dl>
        {row.deployment && (
          <p className="mt-2 text-xs text-slate-400">
            Shipped by {row.deployment.pipeline}{" "}
            {row.deployment.url ? (
              <a className="text-sky-300 hover:underline" href={row.deployment.url} rel="noreferrer" target="_blank">
                {row.deployment.build_number}
              </a>
            ) : (
              row.deployment.build_number
            )}
          </p>
        )}
      </div>
      <div className="min-w-0">
        <h4 className="text-sm font-semibold text-slate-100">Work items ({row.work_items.length})</h4>
        {row.work_items.length === 0 ? (
          <p className="mt-2 text-xs text-slate-500">
            {row.unresolved_work_item_refs.length
              ? `Mentions AB#${row.unresolved_work_item_refs.join(", AB#")}, which isn't synced from Azure DevOps.`
              : "No linked story. Add AB#<story id> to the PR title or description, or link the PR from the story in Azure Boards."}
          </p>
        ) : (
          <ul className="mt-2 space-y-2 text-xs">
            {row.work_items.map((item) => (
              <li className="rounded-lg border border-slate-800 px-3 py-2" key={item.id}>
                {item.url ? (
                  <a className="text-sky-300 hover:underline" href={item.url} rel="noreferrer" target="_blank">
                    #{item.id}
                  </a>
                ) : (
                  `#${item.id}`
                )}{" "}
                <span className="text-slate-200">{item.title}</span>
                <span className="mt-1 block text-slate-500">
                  {item.type} · {item.state ?? "—"}
                  {item.story_id && item.story_id !== item.id ? ` · story #${item.story_id}` : ""} · linked by {item.sources.join(", ")}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import BranchSelect, { type BranchOption } from "./BranchSelect";
import DevOpsIntegrationPage from "./DevOpsIntegrationPage";
import DateTimeRangePicker from "./DateTimeRangePicker";
import { type DateRange, defaultRange, rangeShortLabel, rangeToApi, readStoredRange, storeRange } from "./dateRange";

const BRANCH_STORAGE_KEY = "git-analysis.target-branches";
const RANGE_STORAGE_KEY = "git-analysis.date-range";

/** "Sep 1, 09:00 – Oct 1, 09:00" for the range the metrics cover (local time). */
function formatWindow(summary: DoraSummary): string {
  if (!summary.window_start || !summary.window_end) return `${summary.window_days} days`;
  const format = (iso: string) =>
    new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  return `${format(summary.window_start)} – ${format(summary.window_end)}`;
}

function readStoredBranches(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(BRANCH_STORAGE_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

type TabKey = "team" | "individual" | "devops";
type IndivTabKey = "summary" | "dora" | "ai_metrics" | "reviewers";

type TrendDirection = "up" | "down";

interface Thresholds {
  large_pr_threshold_lines: number;
  followup_good_threshold_hours: number;
  requested_changes_risky_pct: number;
  high_comments_per_pr_threshold: number;
  lead_time_healthy_hours: number;
  first_review_healthy_hours: number;
  review_coverage_good_pct: number;
  approval_rate_good_pct: number;
  change_failure_acceptable_pct: number;
}

interface CommentCategoryItem {
  category: string;
  count: number;
}

interface PrCycleItem {
  developer: string;
  // Display name from config.yaml teams (falls back to the GitHub login).
  label?: string | null;
  cycleTimeHours: number;
}

interface PrsPerWeekItem {
  week: string;
  mergedPrs: number;
}

interface DeveloperItem {
  github_username: string;
  team_name: string | null;
  // Display name from config.yaml teams (falls back to the GitHub login).
  display_name?: string | null;
}

interface TeamItem {
  name: string;
  members: string[];
}

interface TeamsResponse {
  enabled: boolean;
  teams: TeamItem[];
}

interface SnapshotItem {
  name: string;
  size_bytes: number;
  created_at: string;
}

interface SnapshotListResponse {
  directory: string;
  autoload: boolean;
  save_after_sync: boolean;
  snapshots: SnapshotItem[];
}

interface IngestRunStats {
  range?: { start: string; end: string; days: number };
  upserted?: number;
  unchanged?: number;
  github_requests?: number;
  skipped_authors?: number;
  full_refresh?: boolean;
  snapshot?: string;
  error?: string;
  finished_at?: string;
}

interface IngestStatus {
  status: string;
  message: string;
  last_run: IngestRunStats | null;
}

interface TeamPerformance {
  team_name: string;
  average_pr_cycle_time_minutes: number | null;
  total_commits: number;
  total_prs: number;
}

interface DoraSummary {
  window_days: number;
  window_start?: string;
  window_end?: string;
  pull_request_count: number;
  merged_pull_request_count: number;
  reviewed_pull_request_count: number;
  merge_frequency_per_week: number;
  average_lead_time_hours: number | null;
  median_lead_time_hours: number | null;
  average_time_to_first_review_hours: number | null;
  review_coverage_rate: number;
  approval_rate: number;
  change_failure_proxy_rate: number;
  average_recovery_time_hours: number | null;
  recovery_samples: number;
  ghost_merge_count: number;
  ghost_merge_rate: number;
}

interface DoraWeeklyTrendItem {
  week: string;
  merged_prs: number;
  average_lead_time_hours: number | null;
  average_time_to_first_review_hours: number | null;
  change_failure_proxy_rate: number;
  ghost_merges: number;
}

interface TeamDoraMetricsResponse {
  summary: DoraSummary;
  weekly_trends: DoraWeeklyTrendItem[];
}

interface DeveloperDoraMetricsResponse extends TeamDoraMetricsResponse {
  github_username: string;
  team_name: string | null;
}

interface DeveloperAnalyticsPullRequestItem {
  pr_number: number;
  title: string;
  created_at: string | null;
  merged_at: string | null;
  review_comments: number;
  requested_changes: number;
  rework_commits: number;
  size: number;
}

interface DeveloperAnalyticsCategorySummary {
  key: string;
  label: string;
  score: number | null;
  assessment: string;
  evidence: string[];
}

interface DeveloperAnalyticsSummary {
  provider: string;
  narrative_provider?: string;
  confidence: string;
  overview: string;
  categories: DeveloperAnalyticsCategorySummary[];
  highlights: string[];
  risks: string[];
  recommendations: string[];
  strengths: string[];
  improvement_areas: string[];
  coding_standards_score: number | null;
  design_patterns_summary: string;
  dry_vs_wet_observations: string;
  reviewer_rigor_score: number | null;
}

interface ReviewerItem {
  login: string;
  comment_count: number;
}

interface DeveloperAnalyticsResponse {
  github_username: string;
  team_name: string | null;
  languages: string[];
  metrics: {
    pull_request_count: number;
    merged_pull_request_count: number;
    total_review_comments: number;
    average_comments_per_pr: number;
    average_pr_size: number;
    average_changed_files: number;
    largest_pr_size: number;
    comment_density_per_100_lines: number;
    requested_changes_rate: number;
    average_rework_commits_per_pr: number;
    average_cycle_time_hours: number | null;
    average_time_to_first_followup_hours: number | null;
    average_time_to_merge_after_feedback_hours: number | null;
    large_pr_rate: number;
  };
  breakdown: {
    comment_categories: CommentCategoryItem[];
    repeated_issue_categories: Array<{ category: string; count: number; pull_request_count: number }>;
    pull_requests: DeveloperAnalyticsPullRequestItem[];
    reviewers: ReviewerItem[];
  };
  sample: {
    pull_requests: number;
    comment_text_items: number;
    followup_samples: number;
    resolution_samples: number;
  };
  summary: DeveloperAnalyticsSummary;
}

function AdvancedMetricCard({
  label,
  value,
  trendValue,
  trendDirection,
}: {
  label: string;
  value: string;
  trendValue: string;
  trendDirection: TrendDirection;
}) {
  const isUp = trendDirection === "up";

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-sm shadow-slate-950/30">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-slate-400">{label}</p>
          <p className="mt-4 text-3xl font-semibold tracking-tight text-white">{value}</p>
        </div>
        <div
          className={`flex items-center gap-1 rounded-full px-2 py-1 text-xs font-medium ${
            isUp ? "bg-emerald-500/10 text-emerald-300" : "bg-rose-500/10 text-rose-300"
          }`}
        >
          <span>{isUp ? "↑" : "↓"}</span>
          <span>{trendValue}</span>
        </div>
      </div>
    </div>
  );
}

function AnalyticsBullets({ title, items, tone = "slate" }: { title: string; items: string[]; tone?: "slate" | "rose" | "emerald" | "amber" }) {
  const toneClass =
    tone === "rose"
      ? "border-rose-900/40 bg-rose-950/20"
      : tone === "emerald"
        ? "border-emerald-900/40 bg-emerald-950/20"
        : tone === "amber"
          ? "border-amber-900/40 bg-amber-950/20"
          : "border-slate-800 bg-slate-950/30";

  return (
    <div className={`rounded-2xl border p-4 ${toneClass}`}>
      <h3 className="text-sm font-medium text-slate-100">{title}</h3>
      <ul className="mt-3 space-y-2 text-sm text-slate-300">
        {items.map((item) => (
          <li key={item} className="flex gap-2">
            <span className="text-slate-500">•</span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DoraMetricsPanel({
  title,
  subtitle,
  metrics,
  weeklyTrends,
  loading,
  error,
}: {
  title: string;
  subtitle: string;
  metrics: DoraSummary | null;
  weeklyTrends: DoraWeeklyTrendItem[];
  loading: boolean;
  error: string;
}) {
  if (loading) {
    return (
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl shadow-black/20">
        <Spinner label="Loading DORA-inspired metrics…" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-2xl border border-rose-900/50 bg-rose-950/20 p-6 text-sm text-rose-200 shadow-xl shadow-black/20">
        {error}
      </div>
    );
  }

  if (!metrics || metrics.pull_request_count === 0) {
    return (
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 text-sm text-slate-400 shadow-xl shadow-black/20">
        No PR data is available yet for DORA-inspired GitHub delivery metrics.
      </div>
    );
  }

  const cards: Array<{ label: string; value: string; trendValue: string; trendDirection: TrendDirection }> = [
    {
      label: "Merge Frequency",
      value: `${metrics.merge_frequency_per_week.toFixed(1)}/wk`,
      trendValue: `${metrics.merged_pull_request_count} merged`,
      trendDirection: metrics.merge_frequency_per_week >= 1 ? "up" : "down",
    },
    {
      label: "Avg Lead Time",
      value: metrics.average_lead_time_hours != null ? `${metrics.average_lead_time_hours.toFixed(1)}h` : "—",
      trendValue: metrics.median_lead_time_hours != null ? `P50 ${metrics.median_lead_time_hours.toFixed(1)}h` : "No median",
      trendDirection:
        metrics.average_lead_time_hours != null && metrics.average_lead_time_hours <= 48 ? "up" : "down",
    },
    {
      label: "First Review Time",
      value:
        metrics.average_time_to_first_review_hours != null
          ? `${metrics.average_time_to_first_review_hours.toFixed(1)}h`
          : "—",
      trendValue: `${metrics.reviewed_pull_request_count} reviewed`,
      trendDirection:
        metrics.average_time_to_first_review_hours != null && metrics.average_time_to_first_review_hours <= 24
          ? "up"
          : "down",
    },
    {
      label: "Review Coverage",
      value: `${metrics.review_coverage_rate.toFixed(1)}%`,
      trendValue: `${metrics.pull_request_count} PRs`,
      trendDirection: metrics.review_coverage_rate >= 80 ? "up" : "down",
    },
    {
      label: "Approval Rate",
      value: `${metrics.approval_rate.toFixed(1)}%`,
      trendValue: `${metrics.reviewed_pull_request_count} reviewed`,
      trendDirection: metrics.approval_rate >= 60 ? "up" : "down",
    },
    {
      label: "Change Failure Proxy",
      value: `${metrics.change_failure_proxy_rate.toFixed(1)}%`,
      trendValue:
        metrics.average_recovery_time_hours != null
          ? `Recovery ${metrics.average_recovery_time_hours.toFixed(1)}h`
          : `${metrics.recovery_samples} recovery samples`,
      trendDirection: metrics.change_failure_proxy_rate <= 35 ? "up" : "down",
    },
    {
      label: "Ghost Merges",
      value: `${metrics.ghost_merge_count}`,
      trendValue: `${metrics.ghost_merge_rate.toFixed(1)}% merged unreviewed`,
      trendDirection: metrics.ghost_merge_count === 0 ? "up" : "down",
    },
  ];

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl shadow-black/20">
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-400">DORA-inspired GitHub Metrics</p>
          <h2 className="mt-2 text-2xl font-semibold text-white">{title}</h2>
          <p className="mt-2 text-sm text-slate-300">{subtitle}</p>
        </div>
        <span className="rounded-full border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-300">
          {formatWindow(metrics)}
        </span>
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <AdvancedMetricCard
            key={card.label}
            label={card.label}
            value={card.value}
            trendValue={card.trendValue}
            trendDirection={card.trendDirection}
          />
        ))}
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4 shadow-sm shadow-slate-950/20">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-sm font-medium text-slate-200">Weekly Delivery Trend</h3>
            <span className="text-xs text-slate-400">Merged PRs + lead time</span>
          </div>
          <div className="h-80 w-full">
            {weeklyTrends.length === 0 ? (
              <div className="flex h-full items-center justify-center text-sm text-slate-500">No weekly trend data available.</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={weeklyTrends}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                  <XAxis dataKey="week" stroke="#94a3b8" />
                  <YAxis yAxisId="left" stroke="#94a3b8" />
                  <YAxis yAxisId="right" orientation="right" stroke="#94a3b8" />
                  <Tooltip />
                  <Legend />
                  <Bar yAxisId="left" dataKey="merged_prs" fill="#22c55e" name="Merged PRs" radius={[6, 6, 0, 0]} />
                  <Line
                    yAxisId="right"
                    type="monotone"
                    dataKey="average_lead_time_hours"
                    stroke="#38bdf8"
                    strokeWidth={3}
                    name="Avg lead time (hrs)"
                  />
                </ComposedChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4 shadow-sm shadow-slate-950/20">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-sm font-medium text-slate-200">Weekly Review Friction</h3>
            <span className="text-xs text-slate-400">Review response + failure proxy</span>
          </div>
          <div className="h-80 w-full">
            {weeklyTrends.length === 0 ? (
              <div className="flex h-full items-center justify-center text-sm text-slate-500">No weekly friction data available.</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={weeklyTrends}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                  <XAxis dataKey="week" stroke="#94a3b8" />
                  <YAxis yAxisId="left" stroke="#94a3b8" />
                  <YAxis yAxisId="right" orientation="right" stroke="#94a3b8" />
                  <Tooltip />
                  <Legend />
                  <Line
                    yAxisId="left"
                    type="monotone"
                    dataKey="average_time_to_first_review_hours"
                    stroke="#f59e0b"
                    strokeWidth={3}
                    name="First review time (hrs)"
                  />
                  <Line
                    yAxisId="right"
                    type="monotone"
                    dataKey="change_failure_proxy_rate"
                    stroke="#f43f5e"
                    strokeWidth={3}
                    name="Change failure proxy (%)"
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function IndividualPerformancePanel({
  developerName,
  analytics,
  loading,
  error,
  doraMetrics,
  doraWeeklyTrends,
  doraLoading,
  doraError,
  thresholds,
}: {
  developerName: string;
  analytics: DeveloperAnalyticsResponse | null;
  loading: boolean;
  error: string;
  doraMetrics: DoraSummary | null;
  doraWeeklyTrends: DoraWeeklyTrendItem[];
  doraLoading: boolean;
  doraError: string;
  thresholds: Thresholds;
}) {
  const [activeTab, setActiveTab] = useState<IndivTabKey>("summary");

  const isLoading = loading || doraLoading;
  const hasError = error || doraError;

  // Data is cleared when the selection changes, so while loading nothing from the
  // previously selected developer can be on screen.
  if (isLoading) {
    return (
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl shadow-black/20">
        <Spinner label={developerName ? `Analyzing ${developerName}…` : "Loading individual performance data…"} />
      </div>
    );
  }

  if (hasError && !analytics && !doraMetrics) {
    return (
      <div className="rounded-2xl border border-rose-900/50 bg-rose-950/20 p-6 text-sm text-rose-200 shadow-xl shadow-black/20">
        {error || doraError}
      </div>
    );
  }

  if (!analytics && !doraMetrics) {
    return (
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 text-sm text-slate-400 shadow-xl shadow-black/20">
        No saved PR data is available yet. Sync GitHub data to populate analytics.
      </div>
    );
  }

  const tabs: Array<{ key: IndivTabKey; label: string }> = [
    { key: "summary", label: "Summary" },
    { key: "dora", label: "DORA Metrics" },
    { key: "ai_metrics", label: "AI Metrics" },
    { key: "reviewers", label: "Reviewers" },
  ];

  const reviewerCount = analytics?.breakdown?.reviewers?.length ?? 0;
  const languageTags = analytics?.languages ?? [];

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70 shadow-xl shadow-black/20">
      {/* Panel header */}
      <div className="border-b border-slate-800 p-6">
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-400">Individual Performance</p>
            <h2 className="mt-2 text-2xl font-semibold text-white">{developerName || analytics?.github_username || "Developer"}</h2>
            {analytics && (
              <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">{analytics.summary.overview}</p>
            )}
          </div>
          <div className="flex flex-col items-end gap-2">
            {analytics && (
              <div className="flex flex-wrap justify-end gap-2 text-xs text-slate-300">
                <span className="rounded-full border border-slate-700 bg-slate-800 px-3 py-1.5">
                  {analytics.sample.pull_requests} PRs analyzed
                </span>
                <span className="rounded-full border border-slate-700 bg-slate-800 px-3 py-1.5">
                  Confidence: {analytics.summary.confidence}
                </span>
                <span
                  className="rounded-full border border-slate-700 bg-slate-800 px-3 py-1.5"
                  title={
                    analytics.summary.narrative_provider
                      ? `AI analysis: ${analytics.summary.provider}; overview text: ${analytics.summary.narrative_provider}`
                      : undefined
                  }
                >
                  Provider: {analytics.summary.provider}
                </span>
              </div>
            )}
            {languageTags.length > 0 && (
              <div className="flex flex-wrap justify-end gap-1.5">
                {languageTags.map((lang) => (
                  <span
                    key={lang}
                    className="rounded-full border border-indigo-700/60 bg-indigo-900/30 px-2.5 py-0.5 text-xs font-medium text-indigo-300"
                  >
                    {lang}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Inner tab bar */}
        <div className="mt-5 flex gap-1 overflow-x-auto">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => setActiveTab(tab.key)}
              className={`whitespace-nowrap rounded-lg px-4 py-2 text-sm font-medium transition ${
                activeTab === tab.key
                  ? "bg-indigo-600 text-white"
                  : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              }`}
            >
              {tab.label}
              {tab.key === "reviewers" && reviewerCount > 0 && (
                <span className="ml-1.5 rounded-full bg-slate-700 px-1.5 py-0.5 text-xs">
                  {reviewerCount}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      <div className="p-6">
        {/* ── Summary tab ─────────────────────────────────────────── */}
        {activeTab === "summary" && analytics && (
          <div className="space-y-6">
            <div className="grid gap-4 md:grid-cols-3">
              {[
                {
                  label: "Review Comments",
                  value: analytics.metrics.total_review_comments.toString(),
                  trendValue: `${analytics.metrics.average_comments_per_pr.toFixed(1)} / PR`,
                  trendDirection: (analytics.metrics.average_comments_per_pr > thresholds.high_comments_per_pr_threshold ? "down" : "up") as TrendDirection,
                },
                {
                  label: "Requested Changes",
                  value: `${analytics.metrics.requested_changes_rate.toFixed(1)}%`,
                  trendValue: `${analytics.metrics.average_rework_commits_per_pr.toFixed(1)} rework`,
                  trendDirection: (analytics.metrics.requested_changes_rate > thresholds.requested_changes_risky_pct ? "down" : "up") as TrendDirection,
                },
                {
                  label: "Avg First Follow-up",
                  value: analytics.metrics.average_time_to_first_followup_hours != null
                    ? `${analytics.metrics.average_time_to_first_followup_hours.toFixed(1)}h`
                    : "—",
                  trendValue: `${analytics.sample.followup_samples} samples`,
                  trendDirection: (analytics.metrics.average_time_to_first_followup_hours != null &&
                    analytics.metrics.average_time_to_first_followup_hours <= thresholds.followup_good_threshold_hours
                    ? "up" : "down") as TrendDirection,
                },
              ].map((card) => (
                <AdvancedMetricCard key={card.label} {...card} />
              ))}
            </div>

            <div className="grid gap-4 lg:grid-cols-4">
              {analytics.summary.categories.map((category) => (
                <div key={category.key} className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-medium text-slate-100">{category.label}</p>
                    <span className="text-lg font-semibold text-white">{category.score ?? "—"}</span>
                  </div>
                  <p className="mt-2 text-xs uppercase tracking-wide text-slate-400">{category.assessment}</p>
                  <ul className="mt-3 space-y-2 text-sm text-slate-300">
                    {category.evidence.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>

            <div className="grid gap-4 lg:grid-cols-3">
              <AnalyticsBullets title="Highlights" items={analytics.summary.highlights} tone="emerald" />
              <AnalyticsBullets title="Risks" items={analytics.summary.risks} tone="rose" />
              <AnalyticsBullets title="Recommendations" items={analytics.summary.recommendations} />
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <AnalyticsBullets
                title="Strengths"
                items={analytics.summary.strengths?.length ? analytics.summary.strengths : ["Insufficient data to identify specific strengths yet."]}
                tone="emerald"
              />
              <AnalyticsBullets
                title="Improvement Areas"
                items={analytics.summary.improvement_areas?.length ? analytics.summary.improvement_areas : ["No specific improvement areas identified."]}
                tone="amber"
              />
            </div>
          </div>
        )}

        {/* ── DORA Metrics tab ────────────────────────────────────── */}
        {activeTab === "dora" && (
          <div className="space-y-6">
            {doraLoading && (
              <div className="text-sm text-slate-400">Loading DORA metrics…</div>
            )}
            {doraError && !doraMetrics && (
              <div className="rounded-xl border border-rose-900/50 bg-rose-950/20 p-4 text-sm text-rose-200">{doraError}</div>
            )}
            {doraMetrics && doraMetrics.pull_request_count > 0 && (
              <>
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                  {[
                    {
                      label: "Merge Frequency",
                      value: `${doraMetrics.merge_frequency_per_week.toFixed(1)}/wk`,
                      trendValue: `${doraMetrics.merged_pull_request_count} merged`,
                      trendDirection: (doraMetrics.merge_frequency_per_week >= 1 ? "up" : "down") as TrendDirection,
                    },
                    {
                      label: "Avg Lead Time",
                      value: doraMetrics.average_lead_time_hours != null ? `${doraMetrics.average_lead_time_hours.toFixed(1)}h` : "—",
                      trendValue: doraMetrics.median_lead_time_hours != null ? `P50 ${doraMetrics.median_lead_time_hours.toFixed(1)}h` : "No median",
                      trendDirection: (doraMetrics.average_lead_time_hours != null && doraMetrics.average_lead_time_hours <= thresholds.lead_time_healthy_hours ? "up" : "down") as TrendDirection,
                    },
                    {
                      label: "First Review Time",
                      value: doraMetrics.average_time_to_first_review_hours != null ? `${doraMetrics.average_time_to_first_review_hours.toFixed(1)}h` : "—",
                      trendValue: `${doraMetrics.reviewed_pull_request_count} reviewed`,
                      trendDirection: (doraMetrics.average_time_to_first_review_hours != null && doraMetrics.average_time_to_first_review_hours <= thresholds.first_review_healthy_hours ? "up" : "down") as TrendDirection,
                    },
                    {
                      label: "Review Coverage",
                      value: `${doraMetrics.review_coverage_rate.toFixed(1)}%`,
                      trendValue: `${doraMetrics.pull_request_count} PRs`,
                      trendDirection: (doraMetrics.review_coverage_rate >= thresholds.review_coverage_good_pct ? "up" : "down") as TrendDirection,
                    },
                    {
                      label: "Approval Rate",
                      value: `${doraMetrics.approval_rate.toFixed(1)}%`,
                      trendValue: `${doraMetrics.reviewed_pull_request_count} reviewed`,
                      trendDirection: (doraMetrics.approval_rate >= thresholds.approval_rate_good_pct ? "up" : "down") as TrendDirection,
                    },
                    {
                      label: "Change Failure Proxy",
                      value: `${doraMetrics.change_failure_proxy_rate.toFixed(1)}%`,
                      trendValue: doraMetrics.average_recovery_time_hours != null
                        ? `Recovery ${doraMetrics.average_recovery_time_hours.toFixed(1)}h`
                        : `${doraMetrics.recovery_samples} samples`,
                      trendDirection: (doraMetrics.change_failure_proxy_rate <= thresholds.change_failure_acceptable_pct ? "up" : "down") as TrendDirection,
                    },
                    {
                      label: "Ghost Merges",
                      value: `${doraMetrics.ghost_merge_count}`,
                      trendValue: `${doraMetrics.ghost_merge_rate.toFixed(1)}% merged unreviewed`,
                      trendDirection: (doraMetrics.ghost_merge_count === 0 ? "up" : "down") as TrendDirection,
                    },
                  ].map((card) => (
                    <AdvancedMetricCard key={card.label} {...card} />
                  ))}
                </div>

                <div className="grid gap-6 lg:grid-cols-2">
                  <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4 shadow-sm shadow-slate-950/20">
                    <div className="mb-4 flex items-center justify-between">
                      <h3 className="text-sm font-medium text-slate-200">Weekly Delivery Trend</h3>
                      <span className="text-xs text-slate-400">Merged PRs + lead time</span>
                    </div>
                    <div className="h-80 w-full">
                      {doraWeeklyTrends.length === 0 ? (
                        <div className="flex h-full items-center justify-center text-sm text-slate-500">No weekly trend data available.</div>
                      ) : (
                        <ResponsiveContainer width="100%" height="100%">
                          <ComposedChart data={doraWeeklyTrends}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                            <XAxis dataKey="week" stroke="#94a3b8" />
                            <YAxis yAxisId="left" stroke="#94a3b8" />
                            <YAxis yAxisId="right" orientation="right" stroke="#94a3b8" />
                            <Tooltip />
                            <Legend />
                            <Bar yAxisId="left" dataKey="merged_prs" fill="#22c55e" name="Merged PRs" radius={[6, 6, 0, 0]} />
                            <Line yAxisId="right" type="monotone" dataKey="average_lead_time_hours" stroke="#38bdf8" strokeWidth={3} name="Avg lead time (hrs)" />
                          </ComposedChart>
                        </ResponsiveContainer>
                      )}
                    </div>
                  </div>

                  <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4 shadow-sm shadow-slate-950/20">
                    <div className="mb-4 flex items-center justify-between">
                      <h3 className="text-sm font-medium text-slate-200">Weekly Review Friction</h3>
                      <span className="text-xs text-slate-400">Review response + failure proxy</span>
                    </div>
                    <div className="h-80 w-full">
                      {doraWeeklyTrends.length === 0 ? (
                        <div className="flex h-full items-center justify-center text-sm text-slate-500">No weekly friction data available.</div>
                      ) : (
                        <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={doraWeeklyTrends}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                            <XAxis dataKey="week" stroke="#94a3b8" />
                            <YAxis yAxisId="left" stroke="#94a3b8" />
                            <YAxis yAxisId="right" orientation="right" stroke="#94a3b8" />
                            <Tooltip />
                            <Legend />
                            <Line yAxisId="left" type="monotone" dataKey="average_time_to_first_review_hours" stroke="#f59e0b" strokeWidth={3} name="First review time (hrs)" />
                            <Line yAxisId="right" type="monotone" dataKey="change_failure_proxy_rate" stroke="#f43f5e" strokeWidth={3} name="Change failure proxy (%)" />
                          </LineChart>
                        </ResponsiveContainer>
                      )}
                    </div>
                  </div>
                </div>
              </>
            )}
            {(!doraMetrics || doraMetrics.pull_request_count === 0) && !doraLoading && (
              <div className="text-sm text-slate-500">No DORA metric data available yet.</div>
            )}
          </div>
        )}

        {/* ── AI Metrics tab ──────────────────────────────────────── */}
        {activeTab === "ai_metrics" && analytics && (
          <div className="space-y-6">
            {(analytics.summary.coding_standards_score != null || analytics.summary.reviewer_rigor_score != null) && (
              <div className="grid gap-4 md:grid-cols-2">
                {analytics.summary.coding_standards_score != null && (
                  <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
                    <p className="mb-1 text-xs font-medium uppercase tracking-[0.14em] text-slate-400">Coding Standards Score</p>
                    <p className="text-3xl font-bold text-white">{analytics.summary.coding_standards_score}<span className="ml-1 text-base font-normal text-slate-400">/10</span></p>
                  </div>
                )}
                {analytics.summary.reviewer_rigor_score != null && (
                  <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
                    <p className="mb-1 text-xs font-medium uppercase tracking-[0.14em] text-slate-400">Reviewer Rigor Score</p>
                    <p className="text-3xl font-bold text-white">{analytics.summary.reviewer_rigor_score}<span className="ml-1 text-base font-normal text-slate-400">/10</span></p>
                  </div>
                )}
              </div>
            )}

            {(analytics.summary.design_patterns_summary || analytics.summary.dry_vs_wet_observations) && (
              <div className="grid gap-4 lg:grid-cols-2">
                {analytics.summary.design_patterns_summary && (
                  <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
                    <p className="mb-2 text-xs font-medium uppercase tracking-[0.14em] text-slate-400">Design Patterns</p>
                    <p className="text-sm text-slate-300">{analytics.summary.design_patterns_summary}</p>
                  </div>
                )}
                {analytics.summary.dry_vs_wet_observations && (
                  <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
                    <p className="mb-2 text-xs font-medium uppercase tracking-[0.14em] text-slate-400">DRY vs WET Observations</p>
                    <p className="text-sm text-slate-300">{analytics.summary.dry_vs_wet_observations}</p>
                  </div>
                )}
              </div>
            )}

            <div className="grid gap-6 lg:grid-cols-2">
              <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4 shadow-sm shadow-slate-950/20">
                <div className="mb-4 flex items-center justify-between">
                  <h3 className="text-sm font-medium text-slate-200">Review Feedback Themes</h3>
                  <span className="text-xs text-slate-400">{analytics.sample.comment_text_items} saved comment texts</span>
                </div>
                <div className="h-80 w-full">
                  {analytics.breakdown.comment_categories.length === 0 ? (
                    <div className="flex h-full items-center justify-center text-sm text-slate-500">
                      No saved review comment text is available for thematic analysis.
                    </div>
                  ) : (
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={analytics.breakdown.comment_categories} margin={{ top: 12, right: 12, left: 0, bottom: 8 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                        <XAxis dataKey="category" stroke="#94a3b8" tickLine={false} axisLine={false} />
                        <YAxis allowDecimals={false} stroke="#94a3b8" tickLine={false} axisLine={false} />
                        <Tooltip />
                        <Bar dataKey="count" fill="#8b5cf6" name="Comments" radius={[6, 6, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  )}
                </div>
              </div>

              <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4 shadow-sm shadow-slate-950/20">
                <div className="mb-4 flex items-center justify-between">
                  <h3 className="text-sm font-medium text-slate-200">Review Churn by PR</h3>
                  <span className="text-xs text-slate-400">Top 10 saved PRs</span>
                </div>
                <div className="h-80 w-full">
                  {analytics.breakdown.pull_requests.length === 0 ? (
                    <div className="flex h-full items-center justify-center text-sm text-slate-500">No PR breakdown data available.</div>
                  ) : (
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={analytics.breakdown.pull_requests} margin={{ top: 12, right: 12, left: 0, bottom: 8 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                        <XAxis dataKey="pr_number" stroke="#94a3b8" tickLine={false} axisLine={false} />
                        <YAxis allowDecimals={false} stroke="#94a3b8" tickLine={false} axisLine={false} />
                        <Tooltip />
                        <Legend />
                        <Bar dataKey="review_comments" fill="#22d3ee" name="Review comments" radius={[6, 6, 0, 0]} />
                        <Bar dataKey="rework_commits" fill="#f59e0b" name="Rework commits" radius={[6, 6, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── Reviewers tab ───────────────────────────────────────── */}
        {activeTab === "reviewers" && analytics && (
          <div>
            {analytics.breakdown.reviewers?.length === 0 ? (
              <div className="text-sm text-slate-500">No reviewer data available yet.</div>
            ) : (
              <>
                <p className="mb-4 text-xs text-slate-400">Ranked by total comments left on this developer's pull requests.</p>
                <ol className="space-y-2">
                  {analytics.breakdown.reviewers.map((reviewer, idx) => (
                    <li key={reviewer.login} className="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-950/40 px-4 py-3 text-sm">
                      <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                        idx === 0 ? "bg-amber-500 text-black" :
                        idx === 1 ? "bg-slate-400 text-black" :
                        idx === 2 ? "bg-orange-700 text-white" :
                        "bg-slate-800 text-slate-300"
                      }`}>
                        {idx + 1}
                      </span>
                      <span className="flex-1 font-medium text-slate-200">{reviewer.login}</span>
                      <span className="rounded-full border border-slate-700 bg-slate-800 px-2.5 py-0.5 text-xs text-slate-300">
                        {reviewer.comment_count} {reviewer.comment_count === 1 ? "comment" : "comments"}
                      </span>
                    </li>
                  ))}
                </ol>
              </>
            )}
          </div>
        )}

        {activeTab === "ai_metrics" && !analytics && (
          <div className="text-sm text-slate-500">No AI metrics data available yet.</div>
        )}
        {activeTab === "summary" && !analytics && (
          <div className="text-sm text-slate-500">No summary data available yet.</div>
        )}
        {activeTab === "reviewers" && !analytics && (
          <div className="text-sm text-slate-500">No reviewer data available yet.</div>
        )}
      </div>
    </div>
  );
}


function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

async function apiFetch<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { signal });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

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

export default function GitAnalyticsDashboard() {
  const [activeTab, setActiveTab] = useState<TabKey>("team");
  const [selectedDeveloper, setSelectedDeveloper] = useState<string>("");
  // Developer whose individual data is loaded (or loading); null forces a reload.
  const [loadedDeveloper, setLoadedDeveloper] = useState<string | null>(null);
  // "" = all configured teams
  const [selectedTeam, setSelectedTeam] = useState<string>("");
  // PR target (base) branches to include; empty = all branches. Remembered per browser.
  const [selectedBranches, setSelectedBranches] = useState<string[]>(readStoredBranches);
  const [branchOptions, setBranchOptions] = useState<BranchOption[]>([]);
  const [unknownBranchPrs, setUnknownBranchPrs] = useState(0);
  const branchParams = selectedBranches.map((branch) => `branches=${encodeURIComponent(branch)}`).join("&");
  const [teams, setTeams] = useState<TeamItem[]>([]);
  // Date/time range for every page and for Sync. Remembered per browser.
  const [dateRange, setDateRange] = useState<DateRange>(() => readStoredRange(RANGE_STORAGE_KEY, defaultRange("30d")));
  const rangeKey = JSON.stringify(dateRange);
  const [fullRefresh, setFullRefresh] = useState(false);
  const [snapshotInfo, setSnapshotInfo] = useState<SnapshotListResponse | null>(null);
  const [snapshotBusy, setSnapshotBusy] = useState(false);

  const [teamPerf, setTeamPerf] = useState<TeamPerformance[]>([]);
  const [developers, setDevelopers] = useState<DeveloperItem[]>([]);
  const [cycleByDev, setCycleByDev] = useState<PrCycleItem[]>([]);
  const [teamPrsPerWeek, setTeamPrsPerWeek] = useState<PrsPerWeekItem[]>([]);
  const [indivPrsPerWeek, setIndivPrsPerWeek] = useState<PrsPerWeekItem[]>([]);
  const [indivCycleTime, setIndivCycleTime] = useState<PrCycleItem[]>([]);
  const [developerAnalytics, setDeveloperAnalytics] = useState<DeveloperAnalyticsResponse | null>(null);
  const [teamDoraMetrics, setTeamDoraMetrics] = useState<TeamDoraMetricsResponse | null>(null);
  const [developerDoraMetrics, setDeveloperDoraMetrics] = useState<DeveloperDoraMetricsResponse | null>(null);
  const [thresholds, setThresholds] = useState<Thresholds>({
    large_pr_threshold_lines: 600,
    followup_good_threshold_hours: 12,
    requested_changes_risky_pct: 30,
    high_comments_per_pr_threshold: 2,
    lead_time_healthy_hours: 48,
    first_review_healthy_hours: 24,
    review_coverage_good_pct: 80,
    approval_rate_good_pct: 60,
    change_failure_acceptable_pct: 35,
  });

  const [loading, setLoading] = useState(false);
  const [teamLoading, setTeamLoading] = useState(false);
  const [indivLoading, setIndivLoading] = useState(false);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState("");
  const [error, setError] = useState("");
  const [analyticsError, setAnalyticsError] = useState("");
  const [teamDoraError, setTeamDoraError] = useState("");
  const [indivDoraError, setIndivDoraError] = useState("");

  // In-flight requests; starting a new load aborts the previous one so slow
  // responses (e.g. the LLM-backed analytics) never overwrite newer selections.
  const teamAbortRef = useRef<AbortController | null>(null);
  const quickAbortRef = useRef<AbortController | null>(null);
  const analyticsAbortRef = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      teamAbortRef.current?.abort();
      quickAbortRef.current?.abort();
      analyticsAbortRef.current?.abort();
    },
    [],
  );

  const loadSnapshots = useCallback(async () => {
    try {
      setSnapshotInfo(await apiFetch<SnapshotListResponse>("/api/snapshots"));
    } catch {
      setSnapshotInfo(null);
    }
  }, []);

  /** Loads team-level data and returns the developer that should be selected. */
  const loadTeamData = useCallback(async (): Promise<string> => {
    teamAbortRef.current?.abort();
    const controller = new AbortController();
    teamAbortRef.current = controller;
    const { signal } = controller;

    setTeamLoading(true);
    setTeamDoraError("");
    try {
      // Rolling presets ("last 30 days") are resolved at load time.
      const range = rangeToApi(dateRange);
      const params = new URLSearchParams(range);
      selectedBranches.forEach((branch) => params.append("branches", branch));
      if (selectedTeam) params.set("team", selectedTeam);
      const teamQuery = `?${params}`;
      const [perf, cycle, prsWeek, devs, dora, thresh, teamList] = await Promise.all([
        apiFetch<TeamPerformance[]>(`/api/team-performance${teamQuery}`, signal),
        apiFetch<PrCycleItem[]>(`/api/chart/pr-cycle-by-developer${teamQuery}`, signal),
        apiFetch<PrsPerWeekItem[]>(`/api/chart/prs-per-week${teamQuery}`, signal),
        apiFetch<DeveloperItem[]>(`/api/developers${teamQuery}`, signal),
        apiFetch<TeamDoraMetricsResponse>(`/api/dora/team${teamQuery}`, signal),
        apiFetch<Thresholds>("/api/config/thresholds", signal),
        apiFetch<TeamsResponse>("/api/teams", signal),
      ]);
      const branchList = await apiFetch<{ branches: BranchOption[]; unknown: number }>(
        `/api/branches?${new URLSearchParams({ ...range, ...(selectedTeam ? { team: selectedTeam } : {}) })}`,
        signal,
      );
      setBranchOptions(branchList.branches);
      setUnknownBranchPrs(branchList.unknown);
      setTeams(teamList.teams);
      setTeamPerf(perf);
      setCycleByDev(cycle);
      setTeamPrsPerWeek(prsWeek);
      setDevelopers(devs);
      setTeamDoraMetrics(dora);
      setThresholds(thresh);
      // Keep the selected developer only if they belong to the selected team.
      if (devs.some((d) => d.github_username === selectedDeveloper)) return selectedDeveloper;
      const nextDeveloper = devs[0]?.github_username ?? "";
      setSelectedDeveloper(nextDeveloper);
      return nextDeveloper;
    } catch (err) {
      if (!isAbortError(err)) {
        setTeamDoraMetrics(null);
        setTeamDoraError("Failed to load DORA-inspired GitHub delivery metrics.");
      }
      throw err;
    } finally {
      if (teamAbortRef.current === controller) setTeamLoading(false);
    }
  }, [selectedDeveloper, selectedTeam, dateRange, selectedBranches]);

  /** Charts and DORA: quick database queries that depend on the date range. */
  const loadIndividualQuick = useCallback(
    async (dev: string) => {
      quickAbortRef.current?.abort();
      const controller = new AbortController();
      quickAbortRef.current = controller;
      const { signal } = controller;
      const isCurrent = () => quickAbortRef.current === controller;

      setDeveloperDoraMetrics(null);
      setIndivPrsPerWeek([]);
      setIndivCycleTime([]);
      setIndivDoraError("");
      if (!dev) {
        setIndivLoading(false);
        return;
      }
      setIndivLoading(true);
      const encoded = encodeURIComponent(dev);
      const rangeQuery = new URLSearchParams(rangeToApi(dateRange)).toString();
      const suffix = branchParams ? `${rangeQuery}&${branchParams}` : rangeQuery;
      try {
        const [prsWeek, cycleTime, dora] = await Promise.all([
          apiFetch<PrsPerWeekItem[]>(`/api/chart/prs-per-week?developer=${encoded}&${suffix}`, signal),
          apiFetch<PrCycleItem[]>(`/api/chart/pr-cycle-by-developer?${suffix}`, signal),
          apiFetch<DeveloperDoraMetricsResponse>(`/api/developers/${encoded}/dora?${suffix}`, signal),
        ]);
        if (!isCurrent()) return;
        setIndivPrsPerWeek(prsWeek);
        setIndivCycleTime(cycleTime.filter((r) => r.developer === dev));
        setDeveloperDoraMetrics(dora);
      } catch (err) {
        if (isAbortError(err) || !isCurrent()) return;
        setIndivDoraError("Failed to load DORA-inspired GitHub delivery metrics.");
      } finally {
        if (isCurrent()) setIndivLoading(false);
      }
    },
    [dateRange, branchParams],
  );

  /** AI review analytics (may call an LLM); independent of the date range. */
  const loadIndividualAnalytics = useCallback(async (dev: string) => {
    analyticsAbortRef.current?.abort();
    const controller = new AbortController();
    analyticsAbortRef.current = controller;
    const { signal } = controller;
    const isCurrent = () => analyticsAbortRef.current === controller;

    setDeveloperAnalytics(null);
    setAnalyticsError("");
    if (!dev) {
      setAnalyticsLoading(false);
      return;
    }
    setAnalyticsLoading(true);
    try {
      const result = await apiFetch<DeveloperAnalyticsResponse>(
        `/api/developers/${encodeURIComponent(dev)}/analytics${branchParams ? `?${branchParams}` : ""}`,
        signal,
      );
      if (isCurrent()) setDeveloperAnalytics(result);
    } catch (err) {
      if (isAbortError(err) || !isCurrent()) return;
      setAnalyticsError("Failed to load AI developer review analytics.");
    } finally {
      if (isCurrent()) setAnalyticsLoading(false);
    }
  }, [branchParams]);

  /** Loads everything for ``dev``; a new call cancels the previous developer's requests. */
  const loadIndividualData = useCallback(
    async (dev: string) => {
      // Recorded first so nothing from the previous developer is shown under the new name.
      setLoadedDeveloper(dev);
      await Promise.all([loadIndividualQuick(dev), loadIndividualAnalytics(dev)]);
    },
    [loadIndividualQuick, loadIndividualAnalytics],
  );

  /**
   * Reloads team data and, on the Individual tab, the selected developer's data.
   * ``includeAnalytics`` re-runs the LLM-backed analytics too (Refresh / Sync);
   * date range and team changes only reload the range-dependent queries.
   */
  const refreshData = useCallback(
    async (includeAnalytics = true) => {
      setLoading(true);
      setError("");
      try {
        const dev = await loadTeamData();
        if (activeTab !== "individual") {
          // Reload individual data next time the Individual tab is opened.
          setLoadedDeveloper(null);
        } else if (dev === selectedDeveloper) {
          // (A changed selection is loaded by the selection effect below.)
          await (includeAnalytics ? loadIndividualData(dev) : loadIndividualQuick(dev));
        }
      } catch (err) {
        if (!isAbortError(err)) setError("Failed to load data. Make sure the backend is running.");
      } finally {
        setLoading(false);
      }
    },
    [activeTab, loadTeamData, loadIndividualData, loadIndividualQuick, selectedDeveloper],
  );

  // Initial load, and reload whenever the selected team, date range or target branches change.
  useEffect(() => {
    refreshData(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTeam, rangeKey, branchParams]);

  const handleRangeChange = (range: DateRange) => {
    setDateRange(range);
    storeRange(RANGE_STORAGE_KEY, range);
  };

  // The developer analytics depend on the target branches too: reload them on the
  // Individual tab, or the next time it is opened.
  const handleBranchesChange = (branches: string[]) => {
    setSelectedBranches(branches);
    setLoadedDeveloper(null);
    try {
      localStorage.setItem(BRANCH_STORAGE_KEY, JSON.stringify(branches));
    } catch {
      /* storage unavailable: the selection just isn't remembered */
    }
  };

  useEffect(() => {
    loadSnapshots();
  }, [loadSnapshots]);

  // Load individual data (including the LLM-backed analytics) only while the
  // Individual tab is open, and only when the selection changed.
  useEffect(() => {
    if (activeTab === "individual" && selectedDeveloper && loadedDeveloper !== selectedDeveloper) {
      loadIndividualData(selectedDeveloper);
    }
  }, [activeTab, selectedDeveloper, loadedDeveloper, loadIndividualData]);

  const handleSync = async () => {
    setSyncing(true);
    setSyncMessage("");
    setError("");
    try {
      const res = await fetch("/api/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...rangeToApi(dateRange), full_refresh: fullRefresh }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail ?? `Sync failed (${res.status}).`);
        return;
      }
      setSyncMessage(data.message ?? "Ingestion triggered.");
      // Poll until the background ingestion finishes, then reload the dashboard.
      let status: IngestStatus;
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        status = await apiFetch<IngestStatus>("/api/ingest/status");
        if (status.status !== "running") break;
      }
      const run = status.last_run;
      if (run?.error) {
        setError(`Sync failed: ${run.error}`);
      } else {
        setSyncMessage(
          run
            ? `Sync complete: ${run.upserted ?? 0} PR(s) updated, ${run.unchanged ?? 0} unchanged (not downloaded again), ` +
                `${run.github_requests ?? 0} GitHub API request(s)` +
                (run.snapshot ? `. Snapshot saved: ${run.snapshot}.` : ".")
            : "Sync complete.",
        );
      }
      await Promise.all([refreshData(), loadSnapshots()]);
    } catch {
      setError("Sync failed. Check backend logs.");
    } finally {
      setSyncing(false);
    }
  };

  const handleSaveSnapshot = async () => {
    setSnapshotBusy(true);
    setError("");
    try {
      const res = await fetch("/api/snapshots", { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail ?? `Saving the snapshot failed (${res.status}).`);
        return;
      }
      setSyncMessage(`Snapshot saved: ${data.name} (${data.counts.pull_requests} PRs).`);
      await loadSnapshots();
    } catch {
      setError("Saving the snapshot failed. Check backend logs.");
    } finally {
      setSnapshotBusy(false);
    }
  };

  const handleLoadSnapshot = async () => {
    setSnapshotBusy(true);
    setError("");
    try {
      const res = await fetch("/api/snapshots/load", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail ?? `Loading the snapshot failed (${res.status}).`);
        return;
      }
      setSyncMessage(`Snapshot loaded: ${data.name} (${data.counts.pull_requests} PRs).`);
      await refreshData();
    } catch {
      setError("Loading the snapshot failed. Check backend logs.");
    } finally {
      setSnapshotBusy(false);
    }
  };

  const latestSnapshot = snapshotInfo?.snapshots[0] ?? null;

  const isTeamView = activeTab === "team";

  const avgCycleMin =
    teamPerf.length > 0
      ? teamPerf.reduce((s, t) => s + (t.average_pr_cycle_time_minutes ?? 0), 0) / teamPerf.length
      : null;

  // Individual data belongs to another developer until the new load starts.
  const indivStale = loadedDeveloper !== selectedDeveloper;
  const indivDataLoading = indivLoading || indivStale;
  const indivAnalyticsLoading = analyticsLoading || indivStale;

  const teamCards = [
    { label: "Active Developers", value: developers.length.toString(), loading: teamLoading },
    {
      label: "Avg PR Cycle Time",
      value: avgCycleMin != null ? `${(avgCycleMin / 60).toFixed(1)}h` : "—",
      loading: teamLoading,
    },
    {
      label: `PRs Merged (${rangeShortLabel(dateRange)})`,
      value: (teamDoraMetrics?.summary.merged_pull_request_count ?? 0).toString(),
      loading: teamLoading,
    },
  ];

  const selectedDevPerf = developerAnalytics?.metrics.average_cycle_time_hours != null
    ? developerAnalytics.metrics.average_cycle_time_hours
    : cycleByDev.find((r) => r.developer === selectedDeveloper)?.cycleTimeHours;
  const selectedDeveloperName =
    developers.find((d) => d.github_username === selectedDeveloper)?.display_name || selectedDeveloper;
  const individualCards = [
    { label: "Developer", value: selectedDeveloperName || "—", loading: false },
    {
      label: "Avg PR Cycle Time",
      value: selectedDevPerf != null ? `${selectedDevPerf.toFixed(1)}h` : "—",
      loading: indivAnalyticsLoading,
    },
    {
      label: "PRs Analyzed",
      value: developerAnalytics?.metrics.pull_request_count?.toString() ?? "0",
      loading: indivAnalyticsLoading,
    },
  ];

  const overviewCards = isTeamView ? teamCards : individualCards;
  const prData = isTeamView ? teamPrsPerWeek : indivPrsPerWeek;
  const cycleData = isTeamView ? cycleByDev : indivCycleTime;
  const chartsLoading = isTeamView ? teamLoading : indivDataLoading;

  return (
    <div className="min-h-screen bg-slate-950 p-6 text-slate-100 md:p-8">
      <div className="mx-auto max-w-7xl space-y-6">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl shadow-black/20">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div>
              <h1 className="text-2xl font-semibold">Engineering Performance Dashboard</h1>
              <p className="mt-1 text-sm text-slate-400">Track pull request throughput and cycle time trends.</p>
            </div>
            <div className="flex flex-col items-end gap-3 sm:flex-row sm:items-center">
              <DateTimeRangePicker label="Date range" onChange={handleRangeChange} value={dateRange} />
              <button
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-emerald-500 disabled:opacity-50"
                disabled={syncing}
                onClick={handleSync}
                type="button"
              >
                {syncing ? "Syncing…" : "⟳ Sync from GitHub"}
              </button>
              <button
                className="rounded-lg bg-slate-700 px-4 py-2 text-sm font-medium text-slate-200 transition hover:bg-slate-600 disabled:opacity-50"
                disabled={loading}
                onClick={() => refreshData()}
                type="button"
              >
                {loading ? "Loading…" : "↺ Refresh"}
              </button>
              <div className="inline-flex rounded-xl border border-slate-700 bg-slate-800 p-1">
                <button
                  className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                    activeTab === "team" ? "bg-indigo-500 text-white" : "text-slate-300 hover:text-white"
                  }`}
                  onClick={() => setActiveTab("team")}
                  type="button"
                >
                  Team Overview
                </button>
                <button
                  className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                    activeTab === "individual" ? "bg-indigo-500 text-white" : "text-slate-300 hover:text-white"
                  }`}
                  onClick={() => setActiveTab("individual")}
                  type="button"
                >
                  Individual Performance
                </button>
                <button
                  className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                    activeTab === "devops" ? "bg-indigo-500 text-white" : "text-slate-300 hover:text-white"
                  }`}
                  onClick={() => setActiveTab("devops")}
                  type="button"
                >
                  DevOps Integration
                </button>
              </div>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-slate-800 pt-4 text-sm text-slate-400">
            <label className="flex items-center gap-2" title="Re-download every PR in the date range, even unchanged ones">
              <input
                checked={fullRefresh}
                className="h-4 w-4 accent-emerald-500"
                onChange={(e) => setFullRefresh(e.target.checked)}
                type="checkbox"
              />
              Full refresh on sync
            </label>
            <span className="hidden text-slate-700 sm:inline">|</span>
            <span>
              {latestSnapshot
                ? `Last snapshot: ${new Date(latestSnapshot.created_at).toLocaleString()}`
                : "No snapshot saved yet"}
              {snapshotInfo?.save_after_sync ? " (saved automatically after each sync)" : ""}
            </span>
            <button
              className="rounded-lg border border-slate-700 px-3 py-1.5 text-slate-200 transition hover:bg-slate-800 disabled:opacity-50"
              disabled={snapshotBusy || syncing}
              onClick={handleSaveSnapshot}
              type="button"
            >
              Save snapshot
            </button>
            <button
              className="rounded-lg border border-slate-700 px-3 py-1.5 text-slate-200 transition hover:bg-slate-800 disabled:opacity-50"
              disabled={snapshotBusy || syncing || !latestSnapshot}
              onClick={handleLoadSnapshot}
              type="button"
            >
              Load latest snapshot
            </button>
          </div>

          {syncMessage && (
            <p className="mt-3 rounded-lg bg-emerald-900/40 px-4 py-2 text-sm text-emerald-300">{syncMessage}</p>
          )}
          {error && <p className="mt-3 rounded-lg bg-red-900/40 px-4 py-2 text-sm text-red-300">{error}</p>}
        </div>

        {activeTab === "devops" ? (
          <DevOpsIntegrationPage
            branchOptions={branchOptions}
            dateRange={dateRange}
            onBranchesChange={handleBranchesChange}
            onDateRangeChange={handleRangeChange}
            selectedBranches={selectedBranches}
            unknownBranchPrs={unknownBranchPrs}
          />
        ) : (
          <>
        {(teams.length > 0 || branchOptions.length > 0 || selectedBranches.length > 0 || (!isTeamView && developers.length > 0)) && (
          <div className="flex flex-wrap items-center gap-3">
            <BranchSelect
              onChange={handleBranchesChange}
              options={branchOptions}
              selected={selectedBranches}
              unknown={unknownBranchPrs}
            />
            {teams.length > 0 && (
              <>
                <label className="text-sm text-slate-400" htmlFor="team-select">
                  Team:
                </label>
                <select
                  id="team-select"
                  className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:outline-none"
                  onChange={(e) => setSelectedTeam(e.target.value)}
                  value={selectedTeam}
                >
                  <option value="">All teams</option>
                  {teams.map((t) => (
                    <option key={t.name} value={t.name}>
                      {t.name} ({t.members.length})
                    </option>
                  ))}
                </select>
              </>
            )}
            {!isTeamView && developers.length > 0 && (
              <>
                <label className="text-sm text-slate-400" htmlFor="dev-select">
                  Developer:
                </label>
                <select
                  id="dev-select"
                  className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:outline-none"
                  onChange={(e) => setSelectedDeveloper(e.target.value)}
                  value={selectedDeveloper}
                >
                  {developers.map((d) => (
                    <option key={d.github_username} value={d.github_username}>
                      {d.display_name && d.display_name !== d.github_username
                        ? `${d.display_name} (${d.github_username})`
                        : d.github_username}
                    </option>
                  ))}
                </select>
              </>
            )}
          </div>
        )}

        <div className="grid gap-4 md:grid-cols-3">
          {overviewCards.map((card) => (
            <div key={card.label} className="rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-lg shadow-black/10">
              <p className="text-xs uppercase tracking-wide text-slate-400">{card.label}</p>
              <div className="mt-2 flex h-8 items-center text-2xl font-semibold text-slate-50">
                {card.loading ? <Spinner /> : card.value}
              </div>
            </div>
          ))}
        </div>

        {isTeamView ? (
          <DoraMetricsPanel
            title={selectedTeam ? `${selectedTeam} Delivery Performance` : "Team Delivery Performance"}
            subtitle="GitHub-native proxies for DORA delivery, lead time, quality, and review flow."
            metrics={teamDoraMetrics?.summary ?? null}
            weeklyTrends={teamDoraMetrics?.weekly_trends ?? []}
            loading={teamLoading}
            error={teamDoraError}
          />
        ) : null}

        <div className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-2xl border border-slate-800 bg-slate-900 p-5 shadow-lg shadow-black/10">
            <h2 className="mb-4 text-sm font-medium text-slate-300">
              {isTeamView ? "Average PR Cycle Time by Developer" : "Average PR Cycle Time (selected developer)"}
            </h2>
            <div className="h-80 w-full">
              {chartsLoading ? (
                <div className="flex h-full items-center justify-center">
                  <Spinner label="Loading…" />
                </div>
              ) : cycleData.length === 0 ? (
                <div className="flex h-full items-center justify-center text-sm text-slate-500">
                  No data — click <span className="mx-1 font-medium text-emerald-400">Sync from GitHub</span> to ingest data.
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={cycleData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                    <XAxis dataKey={(row: PrCycleItem) => row.label || row.developer} stroke="#94a3b8" />
                    <YAxis stroke="#94a3b8" />
                    <Tooltip />
                    <Legend />
                    <Bar dataKey="cycleTimeHours" fill="#6366f1" name="Cycle Time (hrs)" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          <div className="rounded-2xl border border-slate-800 bg-slate-900 p-5 shadow-lg shadow-black/10">
            <h2 className="mb-4 text-sm font-medium text-slate-300">Total PRs Merged per Week</h2>
            <div className="h-80 w-full">
              {chartsLoading ? (
                <div className="flex h-full items-center justify-center">
                  <Spinner label="Loading…" />
                </div>
              ) : prData.length === 0 ? (
                <div className="flex h-full items-center justify-center text-sm text-slate-500">
                  No data — click <span className="mx-1 font-medium text-emerald-400">Sync from GitHub</span> to ingest data.
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={prData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                    <XAxis dataKey="week" stroke="#94a3b8" />
                    <YAxis stroke="#94a3b8" />
                    <Tooltip />
                    <Legend />
                    <Line
                      dataKey="mergedPrs"
                      dot={{ fill: "#22d3ee", r: 4, strokeWidth: 2 }}
                      name="Merged PRs"
                      stroke="#22d3ee"
                      strokeWidth={3}
                      type="monotone"
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        </div>

        {!isTeamView && (
          <IndividualPerformancePanel
            developerName={selectedDeveloperName}
            analytics={developerAnalytics}
            error={analyticsError}
            loading={indivAnalyticsLoading}
            doraMetrics={developerDoraMetrics?.summary ?? null}
            doraWeeklyTrends={developerDoraMetrics?.weekly_trends ?? []}
            doraLoading={indivDataLoading}
            doraError={indivDoraError}
            thresholds={thresholds}
          />
        )}
          </>
        )}
      </div>
    </div>
  );
}

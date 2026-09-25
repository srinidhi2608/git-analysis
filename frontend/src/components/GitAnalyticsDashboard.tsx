import { useCallback, useEffect, useState } from "react";
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

type TabKey = "team" | "individual";

type TrendDirection = "up" | "down";

interface CommentCategoryItem {
  category: string;
  count: number;
}

interface PrCycleItem {
  developer: string;
  cycleTimeHours: number;
}

interface PrsPerWeekItem {
  week: string;
  mergedPrs: number;
}

interface DeveloperItem {
  github_username: string;
  team_name: string | null;
}

interface TeamPerformance {
  team_name: string;
  average_pr_cycle_time_minutes: number | null;
  total_commits: number;
  total_prs: number;
}

interface DoraSummary {
  window_days: number;
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
}

interface DoraWeeklyTrendItem {
  week: string;
  merged_prs: number;
  average_lead_time_hours: number | null;
  average_time_to_first_review_hours: number | null;
  change_failure_proxy_rate: number;
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
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 text-sm text-slate-400 shadow-xl shadow-black/20">
        Loading DORA-inspired metrics…
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
          Last {metrics.window_days} days
        </span>
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
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

function DeveloperAnalyticsPanel({
  analytics,
  loading,
  error,
}: {
  analytics: DeveloperAnalyticsResponse | null;
  loading: boolean;
  error: string;
}) {
  if (loading) {
    return (
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 text-sm text-slate-400 shadow-xl shadow-black/20">
        Loading developer review analytics…
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

  if (!analytics || analytics.metrics.pull_request_count === 0) {
    return (
      <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 text-sm text-slate-400 shadow-xl shadow-black/20">
        No saved PR review data is available for this developer yet. Sync GitHub data to populate AI review analytics.
      </div>
    );
  }

  const summaryCards: Array<{
    label: string;
    value: string;
    trendValue: string;
    trendDirection: TrendDirection;
  }> = [
    {
      label: "Review Comments",
      value: analytics.metrics.total_review_comments.toString(),
      trendValue: `${analytics.metrics.average_comments_per_pr.toFixed(1)} / PR`,
      trendDirection: analytics.metrics.average_comments_per_pr > 2 ? "down" : "up" as TrendDirection,
    },
    {
      label: "Requested Changes",
      value: `${analytics.metrics.requested_changes_rate.toFixed(1)}%`,
      trendValue: `${analytics.metrics.average_rework_commits_per_pr.toFixed(1)} rework`,
      trendDirection: analytics.metrics.requested_changes_rate > 30 ? "down" : "up" as TrendDirection,
    },
    {
      label: "Avg First Follow-up",
      value:
        analytics.metrics.average_time_to_first_followup_hours != null
          ? `${analytics.metrics.average_time_to_first_followup_hours.toFixed(1)}h`
          : "—",
      trendValue: `${analytics.sample.followup_samples} samples`,
      trendDirection:
        analytics.metrics.average_time_to_first_followup_hours != null &&
        analytics.metrics.average_time_to_first_followup_hours <= 12
          ? "up"
          : "down",
    },
  ];

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl shadow-black/20">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-400">AI Developer Review Analytics</p>
          <h2 className="mt-2 text-2xl font-semibold text-white">{analytics.github_username}</h2>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300">{analytics.summary.overview}</p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs text-slate-300">
          <span className="rounded-full border border-slate-700 bg-slate-800 px-3 py-1.5">
            {analytics.sample.pull_requests} PRs analyzed
          </span>
          <span className="rounded-full border border-slate-700 bg-slate-800 px-3 py-1.5">
            Confidence: {analytics.summary.confidence}
          </span>
          <span className="rounded-full border border-slate-700 bg-slate-800 px-3 py-1.5">
            Provider: {analytics.summary.provider}
          </span>
        </div>
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-3">
        {summaryCards.map((card) => (
          <AdvancedMetricCard
            key={card.label}
            label={card.label}
            value={card.value}
            trendValue={card.trendValue}
            trendDirection={card.trendDirection}
          />
        ))}
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-4">
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

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
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

      <div className="mt-6 grid gap-4 lg:grid-cols-3">
        <AnalyticsBullets title="Highlights" items={analytics.summary.highlights} tone="emerald" />
        <AnalyticsBullets title="Risks" items={analytics.summary.risks} tone="rose" />
        <AnalyticsBullets title="Recommendations" items={analytics.summary.recommendations} />
      </div>

      {/* Strengths & Improvement Areas */}
      <div className="mt-6 grid gap-4 lg:grid-cols-2">
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

      {/* AI Code Quality Signals */}
      {(analytics.summary.design_patterns_summary || analytics.summary.dry_vs_wet_observations) && (
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
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

      {/* Reviewer Ranking */}
      {analytics.breakdown.reviewers?.length > 0 && (
        <div className="mt-6 rounded-2xl border border-slate-800 bg-slate-950/40 p-4 shadow-sm shadow-slate-950/20">
          <h3 className="mb-4 text-sm font-medium text-slate-200">Top Reviewers by Comments</h3>
          <ol className="space-y-2">
            {analytics.breakdown.reviewers.map((reviewer, idx) => (
              <li key={reviewer.login} className="flex items-center gap-3 text-sm">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-800 text-xs font-semibold text-slate-300">
                  {idx + 1}
                </span>
                <span className="flex-1 font-medium text-slate-200">{reviewer.login}</span>
                <span className="rounded-full border border-slate-700 bg-slate-800 px-2.5 py-0.5 text-xs text-slate-300">
                  {reviewer.comment_count} {reviewer.comment_count === 1 ? "comment" : "comments"}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export default function GitAnalyticsDashboard() {
  const [activeTab, setActiveTab] = useState<TabKey>("team");
  const [selectedDeveloper, setSelectedDeveloper] = useState<string>("");
  const [lookbackDays, setLookbackDays] = useState<number>(7);

  const [teamPerf, setTeamPerf] = useState<TeamPerformance[]>([]);
  const [developers, setDevelopers] = useState<DeveloperItem[]>([]);
  const [cycleByDev, setCycleByDev] = useState<PrCycleItem[]>([]);
  const [teamPrsPerWeek, setTeamPrsPerWeek] = useState<PrsPerWeekItem[]>([]);
  const [indivPrsPerWeek, setIndivPrsPerWeek] = useState<PrsPerWeekItem[]>([]);
  const [indivCycleTime, setIndivCycleTime] = useState<PrCycleItem[]>([]);
  const [developerAnalytics, setDeveloperAnalytics] = useState<DeveloperAnalyticsResponse | null>(null);
  const [teamDoraMetrics, setTeamDoraMetrics] = useState<TeamDoraMetricsResponse | null>(null);
  const [developerDoraMetrics, setDeveloperDoraMetrics] = useState<DeveloperDoraMetricsResponse | null>(null);

  const [loading, setLoading] = useState(false);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [doraLoading, setDoraLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState("");
  const [error, setError] = useState("");
  const [analyticsError, setAnalyticsError] = useState("");
  const [doraError, setDoraError] = useState("");

  const loadTeamData = useCallback(async () => {
    setDoraLoading(true);
    setDoraError("");
    try {
      const [perf, cycle, prsWeek, devs, dora] = await Promise.all([
        apiFetch<TeamPerformance[]>("/api/team-performance"),
        apiFetch<PrCycleItem[]>("/api/chart/pr-cycle-by-developer"),
        apiFetch<PrsPerWeekItem[]>("/api/chart/prs-per-week"),
        apiFetch<DeveloperItem[]>("/api/developers"),
        apiFetch<TeamDoraMetricsResponse>("/api/dora/team"),
      ]);
      setTeamPerf(perf);
      setCycleByDev(cycle);
      setTeamPrsPerWeek(prsWeek);
      setDevelopers(devs);
      setTeamDoraMetrics(dora);
      if (!selectedDeveloper && devs.length > 0) {
        setSelectedDeveloper(devs[0].github_username);
      }
    } catch {
      setTeamDoraMetrics(null);
      setDoraError("Failed to load DORA-inspired GitHub delivery metrics.");
      throw new Error("team dora metrics failed");
    } finally {
      setDoraLoading(false);
    }
  }, [selectedDeveloper]);

  const loadIndividualData = useCallback(async (dev: string) => {
    if (!dev) {
      setDeveloperAnalytics(null);
      setDeveloperDoraMetrics(null);
      return;
    }

    setAnalyticsLoading(true);
    setDoraLoading(true);
    setAnalyticsError("");
    setDoraError("");
    try {
      const [prsWeek, cycleTime, analytics, dora] = await Promise.all([
        apiFetch<PrsPerWeekItem[]>(`/api/chart/prs-per-week?developer=${encodeURIComponent(dev)}`),
        apiFetch<PrCycleItem[]>("/api/chart/pr-cycle-by-developer"),
        apiFetch<DeveloperAnalyticsResponse>(`/api/developers/${encodeURIComponent(dev)}/analytics`),
        apiFetch<DeveloperDoraMetricsResponse>(`/api/developers/${encodeURIComponent(dev)}/dora`),
      ]);
      setIndivPrsPerWeek(prsWeek);
      setIndivCycleTime(cycleTime.filter((r) => r.developer === dev));
      setDeveloperAnalytics(analytics);
      setDeveloperDoraMetrics(dora);
    } catch {
      setDeveloperAnalytics(null);
      setDeveloperDoraMetrics(null);
      setAnalyticsError("Failed to load AI developer review analytics.");
      setDoraError("Failed to load DORA-inspired GitHub delivery metrics.");
    } finally {
      setAnalyticsLoading(false);
      setDoraLoading(false);
    }
  }, []);

  const refreshData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      await loadTeamData();
      if (selectedDeveloper) await loadIndividualData(selectedDeveloper);
    } catch {
      setError("Failed to load data. Make sure the backend is running.");
    } finally {
      setLoading(false);
    }
  }, [loadTeamData, loadIndividualData, selectedDeveloper]);

  useEffect(() => {
    refreshData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selectedDeveloper) loadIndividualData(selectedDeveloper);
  }, [selectedDeveloper, loadIndividualData]);

  const handleSync = async () => {
    setSyncing(true);
    setSyncMessage("");
    setError("");
    try {
      const res = await fetch("/api/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lookback_days: Number(lookbackDays) || 7 }),
      });
      const data = await res.json();
      setSyncMessage(data.message ?? "Ingestion triggered.");
      setTimeout(() => refreshData(), 3000);
    } catch {
      setError("Sync failed. Check backend logs.");
    } finally {
      setSyncing(false);
    }
  };

  const isTeamView = activeTab === "team";

  const totalPrs = teamPerf.reduce((s, t) => s + t.total_prs, 0);
  const avgCycleMin =
    teamPerf.length > 0
      ? teamPerf.reduce((s, t) => s + (t.average_pr_cycle_time_minutes ?? 0), 0) / teamPerf.length
      : null;

  const teamCards = [
    { label: "Active Developers", value: developers.length.toString() },
    { label: "Avg PR Cycle Time", value: avgCycleMin != null ? `${(avgCycleMin / 60).toFixed(1)}h` : "—" },
    { label: "PRs Merged (30d)", value: totalPrs.toString() },
  ];

  const selectedDevPerf = developerAnalytics?.metrics.average_cycle_time_hours != null
    ? developerAnalytics.metrics.average_cycle_time_hours
    : cycleByDev.find((r) => r.developer === selectedDeveloper)?.cycleTimeHours;
  const individualCards = [
    { label: "Developer", value: selectedDeveloper || "—" },
    { label: "Avg PR Cycle Time", value: selectedDevPerf != null ? `${selectedDevPerf.toFixed(1)}h` : "—" },
    { label: "PRs Analyzed", value: developerAnalytics?.metrics.pull_request_count?.toString() ?? "0" },
  ];

  const overviewCards = isTeamView ? teamCards : individualCards;
  const prData = isTeamView ? teamPrsPerWeek : indivPrsPerWeek;
  const cycleData = isTeamView ? cycleByDev : indivCycleTime;

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
              <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200">
                <span>Lookback</span>
                <input
                  aria-label="Lookback days"
                  className="w-16 rounded-md border border-slate-600 bg-slate-900 px-2 py-1 text-right text-slate-100 focus:outline-none"
                  max={365}
                  min={1}
                  onChange={(event) => setLookbackDays(Number(event.target.value) || 7)}
                  type="number"
                  value={lookbackDays}
                />
                <span className="text-xs uppercase tracking-wide text-slate-400">days</span>
              </label>
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
                onClick={refreshData}
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
              </div>
            </div>
          </div>

          {syncMessage && (
            <p className="mt-3 rounded-lg bg-emerald-900/40 px-4 py-2 text-sm text-emerald-300">{syncMessage}</p>
          )}
          {error && <p className="mt-3 rounded-lg bg-red-900/40 px-4 py-2 text-sm text-red-300">{error}</p>}
        </div>

        {!isTeamView && developers.length > 0 && (
          <div className="flex items-center gap-3">
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
                  {d.github_username}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="grid gap-4 md:grid-cols-3">
          {overviewCards.map((card) => (
            <div key={card.label} className="rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-lg shadow-black/10">
              <p className="text-xs uppercase tracking-wide text-slate-400">{card.label}</p>
              <p className="mt-2 text-2xl font-semibold text-slate-50">{card.value}</p>
            </div>
          ))}
        </div>

        {isTeamView ? (
          <DoraMetricsPanel
            title="Team Delivery Performance"
            subtitle="GitHub-native proxies for DORA delivery, lead time, quality, and review flow."
            metrics={teamDoraMetrics?.summary ?? null}
            weeklyTrends={teamDoraMetrics?.weekly_trends ?? []}
            loading={doraLoading}
            error={doraError}
          />
        ) : (
          <DoraMetricsPanel
            title={`${selectedDeveloper || "Developer"} Delivery Performance`}
            subtitle="Individual GitHub delivery metrics inspired by DORA, derived from saved PR and review activity."
            metrics={developerDoraMetrics?.summary ?? null}
            weeklyTrends={developerDoraMetrics?.weekly_trends ?? []}
            loading={doraLoading}
            error={doraError}
          />
        )}

        <div className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-2xl border border-slate-800 bg-slate-900 p-5 shadow-lg shadow-black/10">
            <h2 className="mb-4 text-sm font-medium text-slate-300">
              {isTeamView ? "Average PR Cycle Time by Developer" : "Average PR Cycle Time (selected developer)"}
            </h2>
            <div className="h-80 w-full">
              {cycleData.length === 0 ? (
                <div className="flex h-full items-center justify-center text-sm text-slate-500">
                  No data — click <span className="mx-1 font-medium text-emerald-400">Sync from GitHub</span> to ingest data.
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={cycleData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                    <XAxis dataKey="developer" stroke="#94a3b8" />
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
              {prData.length === 0 ? (
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
          <DeveloperAnalyticsPanel
            analytics={developerAnalytics}
            error={analyticsError}
            loading={analyticsLoading}
          />
        )}
      </div>
    </div>
  );
}

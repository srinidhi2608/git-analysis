import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

type TabKey = "team" | "individual";

type TrendDirection = "up" | "down";

interface PullRequestData {
  number: number;
  title: string;
  createdAt: string;
  mergedAt: string | null;
  closedAt: string | null;
  author: { login: string };
  reviews: { totalCount: number };
  comments: { totalCount: number };
  commits: { totalCount: number };
  reviewDecision: string | null;
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

const mockAdvancedPullRequests: PullRequestData[] = [
  {
    number: 417,
    title: "Fix race condition in auth token refresh",
    createdAt: "2024-06-04T09:00:00Z",
    mergedAt: "2024-06-04T18:30:00Z",
    closedAt: "2024-06-04T18:30:00Z",
    author: { login: "srinidhi2608" },
    reviews: { totalCount: 0 },
    comments: { totalCount: 2 },
    commits: { totalCount: 17 },
    reviewDecision: "MERGED",
  },
  {
    number: 420,
    title: "Improve dashboard loading state",
    createdAt: "2024-06-05T08:15:00Z",
    mergedAt: "2024-06-06T12:45:00Z",
    closedAt: "2024-06-06T12:45:00Z",
    author: { login: "amanda" },
    reviews: { totalCount: 2 },
    comments: { totalCount: 5 },
    commits: { totalCount: 7 },
    reviewDecision: "MERGED",
  },
  {
    number: 431,
    title: "Refactor PR summary query",
    createdAt: "2024-06-07T13:00:00Z",
    mergedAt: null,
    closedAt: "2024-06-08T10:00:00Z",
    author: { login: "jordan" },
    reviews: { totalCount: 1 },
    comments: { totalCount: 4 },
    commits: { totalCount: 9 },
    reviewDecision: "CHANGES_REQUESTED",
  },
  {
    number: 438,
    title: "Add GraphQL review metrics export",
    createdAt: "2024-06-08T11:00:00Z",
    mergedAt: "2024-06-08T19:15:00Z",
    closedAt: "2024-06-08T19:15:00Z",
    author: { login: "nina" },
    reviews: { totalCount: 3 },
    comments: { totalCount: 7 },
    commits: { totalCount: 12 },
    reviewDecision: "MERGED",
  },
  {
    number: 445,
    title: "Reduce API payload for repository metrics",
    createdAt: "2024-06-09T15:30:00Z",
    mergedAt: "2024-06-10T09:00:00Z",
    closedAt: "2024-06-10T09:00:00Z",
    author: { login: "ravi" },
    reviews: { totalCount: 0 },
    comments: { totalCount: 1 },
    commits: { totalCount: 5 },
    reviewDecision: "MERGED",
  },
];

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

export function AdvancedMetricsView({
  pullRequests = mockAdvancedPullRequests,
}: {
  pullRequests?: PullRequestData[];
}) {
  const HIGH_COMMITS_THRESHOLD = 8;
  const HIGH_ABANDONMENT_THRESHOLD = 20;

  const metrics = useMemo(() => {
    const totalPrs = pullRequests.length;
    const mergedPrs = pullRequests.filter((pr) => pr.mergedAt !== null);
    const ghostMerges = mergedPrs.filter((pr) => pr.reviews.totalCount === 0).length;
    const totalCommits = pullRequests.reduce((sum, pr) => sum + pr.commits.totalCount, 0);
    const avgCommitsPerPr = totalPrs === 0 ? 0 : totalCommits / totalPrs;
    const abandonedPrs = pullRequests.filter((pr) => pr.closedAt && !pr.mergedAt).length;
    const abandonmentRate = totalPrs === 0 ? 0 : (abandonedPrs / totalPrs) * 100;

    return {
      ghostMerges,
      avgCommitsPerPr,
      abandonmentRate,
      mergedPrs,
      totalPrs,
    };
  }, [pullRequests]);

  const scatterData = useMemo(
    () =>
      pullRequests
        .filter((pr) => pr.createdAt && pr.mergedAt)
        .map((pr) => {
          const createdAt = new Date(pr.createdAt).getTime();
          const mergedAt = new Date(pr.mergedAt ?? "").getTime();
          const cycleTimeHours = Number.isFinite(createdAt) && Number.isFinite(mergedAt) ? (mergedAt - createdAt) / 3_600_000 : 0;

          return {
            x: pr.commits.totalCount,
            y: cycleTimeHours,
            title: pr.title,
            author: pr.author.login,
          };
        }),
    [pullRequests],
  );

  const reviewCultureData = useMemo(() => {
    const grouped = new Map<string, { author: string; zeroReviews: number; onePlusReviews: number }>();

    for (const pr of pullRequests) {
      const current = grouped.get(pr.author.login) ?? { author: pr.author.login, zeroReviews: 0, onePlusReviews: 0 };
      if (pr.reviews.totalCount === 0) {
        current.zeroReviews += 1;
      } else {
        current.onePlusReviews += 1;
      }
      grouped.set(pr.author.login, current);
    }

    return Array.from(grouped.values());
  }, [pullRequests]);

  const ghostRate = metrics.totalPrs === 0 ? 0 : (metrics.ghostMerges / metrics.totalPrs) * 100;
  const avgCommitsTrend: TrendDirection = metrics.avgCommitsPerPr > HIGH_COMMITS_THRESHOLD ? "up" : "down";
  const abandonmentDirection: TrendDirection = metrics.abandonmentRate > HIGH_ABANDONMENT_THRESHOLD ? "up" : "down";
  const ghostTrendDirection: TrendDirection = metrics.ghostMerges > 0 ? "down" : "up";

  const cards: Array<{
    label: string;
    value: string;
    trendValue: string;
    trendDirection: TrendDirection;
  }> = [
    {
      label: "Ghost Merges",
      value: metrics.ghostMerges.toString(),
      trendValue: `${ghostRate.toFixed(0)}%`,
      trendDirection: ghostTrendDirection,
    },
    {
      label: "Avg Commits per PR",
      value: metrics.avgCommitsPerPr.toFixed(1),
      trendValue: metrics.avgCommitsPerPr > HIGH_COMMITS_THRESHOLD ? "High" : "Low",
      trendDirection: avgCommitsTrend,
    },
    {
      label: "PR Abandonment Rate",
      value: `${metrics.abandonmentRate.toFixed(1)}%`,
      trendValue: `${metrics.abandonmentRate.toFixed(0)}%`,
      trendDirection: abandonmentDirection,
    },
  ];

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl shadow-black/20">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-400">Advanced PR Analytics</p>
          <h2 className="mt-2 text-2xl font-semibold text-white">Pull request quality and delivery insights</h2>
        </div>
        <div className="rounded-full border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-300">
          {metrics.totalPrs} tracked PRs
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
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
            <h3 className="text-sm font-medium text-slate-200">PR Complexity vs Cycle Time</h3>
            <span className="text-xs text-slate-400">Merged PRs only</span>
          </div>
          <div className="h-80 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 16, right: 12, bottom: 12, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis
                  type="number"
                  dataKey="x"
                  name="Commits"
                  stroke="#94a3b8"
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  type="number"
                  dataKey="y"
                  name="Cycle time"
                  stroke="#94a3b8"
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(value) => `${value}h`}
                />
                <Tooltip
                  cursor={{ strokeDasharray: "4 4" }}
                  content={({ active, payload }) => {
                    if (!active || !payload || payload.length === 0) return null;
                    const point = payload[0].payload as { title: string; author: string; x: number; y: number };
                    return (
                      <div className="rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-slate-100 shadow-lg shadow-black/10">
                        <p className="font-medium text-white">{point.title}</p>
                        <p className="mt-1 text-slate-400">{point.author}</p>
                        <p className="mt-1">{point.x} commits · {point.y.toFixed(1)}h</p>
                      </div>
                    );
                  }}
                />
                <Scatter data={scatterData} fill="#8b5cf6" />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-950/40 p-4 shadow-sm shadow-slate-950/20">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-sm font-medium text-slate-200">Review Culture by Developer</h3>
            <span className="text-xs text-slate-400">0 reviews vs 1+</span>
          </div>
          <div className="h-80 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={reviewCultureData} margin={{ top: 12, right: 12, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis dataKey="author" stroke="#94a3b8" tickLine={false} axisLine={false} />
                <YAxis allowDecimals={false} stroke="#94a3b8" tickLine={false} axisLine={false} />
                <Tooltip />
                <Legend />
                <Bar dataKey="zeroReviews" stackId="reviews" name="0 reviews" fill="#f59e0b" radius={[0, 0, 0, 0]} />
                <Bar dataKey="onePlusReviews" stackId="reviews" name="1+ reviews" fill="#34d399" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
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

  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState("");
  const [error, setError] = useState("");

  const loadTeamData = useCallback(async () => {
    const [perf, cycle, prsWeek, devs] = await Promise.all([
      apiFetch<TeamPerformance[]>("/api/team-performance"),
      apiFetch<PrCycleItem[]>("/api/chart/pr-cycle-by-developer"),
      apiFetch<PrsPerWeekItem[]>("/api/chart/prs-per-week"),
      apiFetch<DeveloperItem[]>("/api/developers"),
    ]);
    setTeamPerf(perf);
    setCycleByDev(cycle);
    setTeamPrsPerWeek(prsWeek);
    setDevelopers(devs);
    if (!selectedDeveloper && devs.length > 0) {
      setSelectedDeveloper(devs[0].github_username);
    }
  }, [selectedDeveloper]);

  const loadIndividualData = useCallback(async (dev: string) => {
    if (!dev) return;
    const [prsWeek, cycleTime] = await Promise.all([
      apiFetch<PrsPerWeekItem[]>(`/api/chart/prs-per-week?developer=${encodeURIComponent(dev)}`),
      apiFetch<PrCycleItem[]>("/api/chart/pr-cycle-by-developer"),
    ]);
    setIndivPrsPerWeek(prsWeek);
    setIndivCycleTime(cycleTime.filter((r) => r.developer === dev));
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

  const selectedDevPerf = cycleByDev.find((r) => r.developer === selectedDeveloper);
  const indivTotalPrs = indivPrsPerWeek.reduce((s, r) => s + r.mergedPrs, 0);
  const individualCards = [
    { label: "Developer", value: selectedDeveloper || "—" },
    { label: "Avg PR Cycle Time", value: selectedDevPerf ? `${selectedDevPerf.cycleTimeHours.toFixed(1)}h` : "—" },
    { label: "PRs Merged", value: indivTotalPrs.toString() },
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

        {/* Placeholder mock data for chart verification until live PR payload data is wired through the API. */}
        <AdvancedMetricsView pullRequests={mockAdvancedPullRequests} />
      </div>
    </div>
  );
}


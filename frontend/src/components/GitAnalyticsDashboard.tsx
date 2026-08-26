import { useCallback, useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

type TabKey = "team" | "individual";

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

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export default function GitAnalyticsDashboard() {
  const [activeTab, setActiveTab] = useState<TabKey>("team");
  const [selectedDeveloper, setSelectedDeveloper] = useState<string>("");

  // Data states
  const [teamPerf, setTeamPerf] = useState<TeamPerformance[]>([]);
  const [developers, setDevelopers] = useState<DeveloperItem[]>([]);
  const [cycleByDev, setCycleByDev] = useState<PrCycleItem[]>([]);
  const [teamPrsPerWeek, setTeamPrsPerWeek] = useState<PrsPerWeekItem[]>([]);
  const [indivPrsPerWeek, setIndivPrsPerWeek] = useState<PrsPerWeekItem[]>([]);
  const [indivCycleTime, setIndivCycleTime] = useState<PrCycleItem[]>([]);

  // UI states
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
    // Filter cycle time for selected developer
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
      const res = await fetch("/api/ingest", { method: "POST" });
      const data = await res.json();
      setSyncMessage(data.message ?? "Ingestion triggered.");
      // Refresh data after a short delay to allow background processing
      setTimeout(() => refreshData(), 3000);
    } catch {
      setError("Sync failed. Check backend logs.");
    } finally {
      setSyncing(false);
    }
  };

  const isTeamView = activeTab === "team";

  // Compute KPI cards
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
        {/* Header */}
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl shadow-black/20">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div>
              <h1 className="text-2xl font-semibold">Engineering Performance Dashboard</h1>
              <p className="mt-1 text-sm text-slate-400">Track pull request throughput and cycle time trends.</p>
            </div>
            <div className="flex flex-col items-end gap-3 sm:flex-row sm:items-center">
              {/* Sync button */}
              <button
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50 transition"
                onClick={handleSync}
                disabled={syncing}
                type="button"
              >
                {syncing ? "Syncing…" : "⟳ Sync from GitHub"}
              </button>
              {/* Refresh button */}
              <button
                className="rounded-lg bg-slate-700 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-slate-600 disabled:opacity-50 transition"
                onClick={refreshData}
                disabled={loading}
                type="button"
              >
                {loading ? "Loading…" : "↺ Refresh"}
              </button>
              {/* Tabs */}
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

          {/* Status messages */}
          {syncMessage && (
            <p className="mt-3 rounded-lg bg-emerald-900/40 px-4 py-2 text-sm text-emerald-300">{syncMessage}</p>
          )}
          {error && (
            <p className="mt-3 rounded-lg bg-red-900/40 px-4 py-2 text-sm text-red-300">{error}</p>
          )}
        </div>

        {/* Developer selector (individual tab) */}
        {!isTeamView && developers.length > 0 && (
          <div className="flex items-center gap-3">
            <label className="text-sm text-slate-400" htmlFor="dev-select">
              Developer:
            </label>
            <select
              id="dev-select"
              className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 focus:outline-none"
              value={selectedDeveloper}
              onChange={(e) => setSelectedDeveloper(e.target.value)}
            >
              {developers.map((d) => (
                <option key={d.github_username} value={d.github_username}>
                  {d.github_username}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* KPI cards */}
        <div className="grid gap-4 md:grid-cols-3">
          {overviewCards.map((card) => (
            <div key={card.label} className="rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-lg shadow-black/10">
              <p className="text-xs uppercase tracking-wide text-slate-400">{card.label}</p>
              <p className="mt-2 text-2xl font-semibold text-slate-50">{card.value}</p>
            </div>
          ))}
        </div>

        {/* Charts */}
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
                    <Bar dataKey="cycleTimeHours" name="Cycle Time (hrs)" fill="#6366f1" radius={[6, 6, 0, 0]} />
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
                      type="monotone"
                      dataKey="mergedPrs"
                      name="Merged PRs"
                      stroke="#22d3ee"
                      strokeWidth={3}
                      dot={{ fill: "#22d3ee", strokeWidth: 2, r: 4 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}


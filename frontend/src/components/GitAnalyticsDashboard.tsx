import { useMemo, useState } from "react";
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

const averagePrCycleByDeveloper = [
  { developer: "Asha", cycleTimeHours: 6.8 },
  { developer: "Rahul", cycleTimeHours: 5.2 },
  { developer: "Nina", cycleTimeHours: 7.1 },
  { developer: "Omar", cycleTimeHours: 4.9 },
  { developer: "Mei", cycleTimeHours: 5.8 },
];

const prsMergedPerWeek = [
  { week: "W1", mergedPrs: 14 },
  { week: "W2", mergedPrs: 19 },
  { week: "W3", mergedPrs: 22 },
  { week: "W4", mergedPrs: 17 },
  { week: "W5", mergedPrs: 25 },
  { week: "W6", mergedPrs: 28 },
];

const individualCycleTime = [
  { week: "W1", cycleTimeHours: 7.4 },
  { week: "W2", cycleTimeHours: 6.8 },
  { week: "W3", cycleTimeHours: 6.3 },
  { week: "W4", cycleTimeHours: 5.9 },
  { week: "W5", cycleTimeHours: 5.5 },
  { week: "W6", cycleTimeHours: 5.1 },
];

const individualPrMerge = [
  { week: "W1", mergedPrs: 2 },
  { week: "W2", mergedPrs: 4 },
  { week: "W3", mergedPrs: 3 },
  { week: "W4", mergedPrs: 5 },
  { week: "W5", mergedPrs: 4 },
  { week: "W6", mergedPrs: 6 },
];

export default function GitAnalyticsDashboard() {
  const [activeTab, setActiveTab] = useState<TabKey>("team");

  const overviewCards = useMemo(() => {
    if (activeTab === "team") {
      return [
        { label: "Active Developers", value: "12" },
        { label: "Avg PR Cycle Time", value: "5.9h" },
        { label: "PRs Merged (6w)", value: "125" },
      ];
    }

    return [
      { label: "Developer", value: "Nina" },
      { label: "Avg PR Cycle Time", value: "5.1h" },
      { label: "PRs Merged (6w)", value: "24" },
    ];
  }, [activeTab]);

  const cycleData = activeTab === "team" ? averagePrCycleByDeveloper : individualCycleTime;
  const prData = activeTab === "team" ? prsMergedPerWeek : individualPrMerge;

  return (
    <div className="min-h-screen bg-slate-950 p-6 text-slate-100 md:p-8">
      <div className="mx-auto max-w-7xl space-y-6">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl shadow-black/20">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div>
              <h1 className="text-2xl font-semibold">Engineering Performance Dashboard</h1>
              <p className="mt-1 text-sm text-slate-400">Track pull request throughput and cycle time trends.</p>
            </div>
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
              {activeTab === "team" ? "Average PR Cycle Time by Developer" : "Average PR Cycle Time Trend"}
            </h2>
            <div className="h-80 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={cycleData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                  <XAxis dataKey={activeTab === "team" ? "developer" : "week"} stroke="#94a3b8" />
                  <YAxis stroke="#94a3b8" />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="cycleTimeHours" name="Cycle Time (hrs)" fill="#6366f1" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="rounded-2xl border border-slate-800 bg-slate-900 p-5 shadow-lg shadow-black/10">
            <h2 className="mb-4 text-sm font-medium text-slate-300">Total PRs Merged per Week</h2>
            <div className="h-80 w-full">
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
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

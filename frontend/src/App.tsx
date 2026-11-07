import { useEffect, useState, type ReactNode } from "react";
import GitAnalyticsDashboard from "./components/GitAnalyticsDashboard";
import StoryAnalyzerPage from "./components/StoryAnalyzerPage";

type PageKey = "dashboard" | "story-analyzer";

const PAGES: { key: PageKey; label: string; hint: string; icon: ReactNode }[] = [
  {
    key: "dashboard",
    label: "Engineering dashboard",
    hint: "Team, individual, DevOps and story quality",
    icon: (
      <svg aria-hidden="true" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24">
        <path d="M4 19V10M10 19V5M16 19v-7M22 19H2" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    key: "story-analyzer",
    label: "Story analyzer",
    hint: "LLM review of one user story",
    icon: (
      <svg aria-hidden="true" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24">
        <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" strokeLinejoin="round" />
        <path d="M14 3v6h6M8 13h5M8 17h8" strokeLinecap="round" />
      </svg>
    ),
  },
];

/** The page from the URL hash (#story-analyzer); anything else is the dashboard. */
function pageFromHash(): PageKey {
  return window.location.hash.replace(/^#\/?/, "") === "story-analyzer" ? "story-analyzer" : "dashboard";
}

function App() {
  const [page, setPage] = useState<PageKey>(pageFromHash);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const onHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHash);
    window.addEventListener("popstate", onHash);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("popstate", onHash);
    };
  }, []);

  const go = (key: PageKey) => {
    // pushState keeps the back button working; the dashboard has no hash.
    window.history.pushState(null, "", key === "dashboard" ? window.location.pathname + window.location.search : `#${key}`);
    setPage(key);
    setMenuOpen(false);
  };

  const nav = (
    <nav aria-label="Pages" className="flex flex-col gap-1">
      {PAGES.map((item) => {
        const active = item.key === page;
        return (
          <button
            aria-current={active ? "page" : undefined}
            className={`flex items-start gap-3 rounded-lg px-3 py-2.5 text-left transition ${
              active ? "bg-indigo-500/15 text-white ring-1 ring-indigo-500/40" : "text-slate-300 hover:bg-slate-800/70 hover:text-white"
            }`}
            key={item.key}
            onClick={() => go(item.key)}
            type="button"
          >
            <span className={active ? "text-indigo-300" : "text-slate-500"}>{item.icon}</span>
            <span>
              <span className="block text-sm font-medium">{item.label}</span>
              <span className="block text-xs text-slate-500">{item.hint}</span>
            </span>
          </button>
        );
      })}
    </nav>
  );

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 lg:flex">
      {/* Small screens: top bar with a menu button */}
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3 lg:hidden">
        <span className="text-sm font-semibold">Git Analysis</span>
        <button
          aria-expanded={menuOpen}
          aria-label="Open navigation"
          className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-200"
          onClick={() => setMenuOpen(!menuOpen)}
          type="button"
        >
          ☰ Menu
        </button>
      </div>
      {menuOpen && <div className="border-b border-slate-800 px-3 py-3 lg:hidden">{nav}</div>}

      {/* Large screens: left panel */}
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col gap-6 border-r border-slate-800 bg-slate-900/60 px-3 py-6 lg:flex">
        <div className="px-3">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">Git Analysis</p>
          <p className="mt-1 text-sm text-slate-300">Engineering insights</p>
        </div>
        {nav}
      </aside>

      <main className="min-w-0 flex-1">
        {/* Both pages stay mounted so switching keeps their filters and results. */}
        <div hidden={page !== "dashboard"}>
          <GitAnalyticsDashboard />
        </div>
        <div hidden={page !== "story-analyzer"}>
          <StoryAnalyzerPage />
        </div>
      </main>
    </div>
  );
}

export default App;

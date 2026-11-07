import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { KIND_COLORS, stageColorMap, type StageKind } from "./stageColors";

// ---------------------------------------------------------------------------
// API types
// ---------------------------------------------------------------------------

interface StoryOption {
  id: number;
  title: string;
  state: string | null;
  type: string;
  assignee: string | null;
  last_analyzed: string | null;
}

interface Section {
  score: number;
  assessment: string;
  strengths: string[];
  issues: string[];
}

interface Segment {
  state: string | null;
  stage: string | null;
  label: string | null;
  kind: StageKind;
  start: string;
  end: string | null;
  hours: number | null;
  assignee: string | null;
}

interface Analysis {
  story: {
    id: number;
    title: string;
    type: string;
    state: string | null;
    url: string | null;
    tags: string[];
    story_points: number | null;
    assignee: string | null;
    release_version: string | null;
  };
  analyzed_at: string;
  provider: string;
  cached: boolean;
  warnings: string[];
  instructions: { path: string; used: boolean; characters: number };
  health: { score: number; level: "Healthy" | "Needs attention" | "At risk"; content_score: number; delivery_score: number; summary: string };
  sections: Record<"description" | "acceptance_criteria" | "evidence" | "comments", Section>;
  comments_insights: { key_points: string[]; unresolved_questions: string[] };
  delivery: { score: number; penalties: { points: number; reason: string }[]; note: string | null };
  suggestions: { area: string; priority: "high" | "medium" | "low"; change: string; example: string }[];
  suggested_acceptance_criteria: string[];
  suggested_tags: string[];
  lifecycle: {
    outcome: string;
    stage: string | null;
    timeline: Segment[];
    stage_hours: Record<string, number>;
    lead_time_hours: number | null;
    cycle_time_hours: number | null;
    age_hours: number | null;
    flow_efficiency: number | null;
    rework_count: number;
    qa_bounces: number;
    reopened: number;
    skipped_stages: string[];
    flags: string[];
    pull_requests: { repository: string; number: number; title: string; status: string; url: string; sources: string[] }[];
    developers: { name: string }[];
    testers: { name: string }[];
    created_at: string | null;
    released_at: string | null;
  };
  stages: { name: string; label: string; kind: StageKind; states: string[]; order: number }[];
  inputs: {
    description: string;
    acceptance_criteria: string;
    attachments: { name: string; url: string }[];
    inline_images: number;
    comments: { author: string | null; created_at: string | null; text: string; images: number }[];
    children: { id: number; type: string; title: string; state: string | null }[];
  };
  context: StoryContext | null;
}

interface StoryContext {
  parents: { id: number; type: string; title: string; state: string | null; url: string | null; attachments: string[]; description: string }[];
  documents: { name: string; source: string; used: boolean; skipped: string | null; characters: number }[];
  review: { score: number | null; assessment: string; gaps: string[]; conflicts: string[]; sources_used: string[] } | null;
}

// Uploads accepted for a deep analysis (the server checks the same limits).
const UPLOAD_ACCEPT = ".docx,.pdf,.txt,.md,.csv,.log,.json,.xml,.yaml,.yml";
const MAX_UPLOADS = 5;
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

function uploadProblem(file: File): string | null {
  const extension = file.name.includes(".") ? `.${file.name.split(".").pop()!.toLowerCase()}` : "";
  if (extension === ".doc") return `${file.name}: old Word format; save it as .docx`;
  if (!UPLOAD_ACCEPT.split(",").includes(extension)) return `${file.name}: only Word (.docx), PDF and text files can be used`;
  if (file.size > MAX_UPLOAD_BYTES) return `${file.name}: larger than 10 MB`;
  return null;
}

function fileSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface Instructions {
  path: string;
  exists: boolean;
  used: boolean;
  text: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const LEVEL_STYLES: Record<Analysis["health"]["level"], string> = {
  Healthy: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  "Needs attention": "border-amber-500/40 bg-amber-500/10 text-amber-300",
  "At risk": "border-rose-500/40 bg-rose-500/10 text-rose-300",
};
const PRIORITY_STYLES = {
  high: "border-rose-500/40 text-rose-300",
  medium: "border-amber-500/40 text-amber-300",
  low: "border-slate-600 text-slate-300",
};
const SECTION_TITLES: [keyof Analysis["sections"], string][] = [
  ["description", "Description"],
  ["acceptance_criteria", "Acceptance criteria"],
  ["evidence", "Evidence"],
  ["comments", "Comments"],
];

function scoreColor(score: number): string {
  return score >= 75 ? "bg-emerald-500" : score >= 50 ? "bg-amber-500" : "bg-rose-500";
}

function duration(hours: number | null | undefined): string {
  if (hours == null) return "—";
  return hours < 24 ? `${hours.toFixed(1)} h` : `${(hours / 24).toFixed(1)} d`;
}

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";
}

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl shadow-black/20 ${className}`}>{children}</section>;
}

function ScoreBar({ score, label }: { score: number; label: string }) {
  return (
    <div>
      <div className="flex items-baseline justify-between text-xs text-slate-400">
        <span>{label}</span>
        <span className="font-mono tabular-nums text-slate-200">{score}/100</span>
      </div>
      <div className="mt-1 h-2 rounded bg-slate-800" role="img" aria-label={`${label}: ${score} out of 100`}>
        <div className={`h-2 rounded ${scoreColor(score)}`} style={{ width: `${score}%` }} />
      </div>
    </div>
  );
}

function Spinner({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-slate-300" role="status">
      <svg aria-hidden="true" className="h-5 w-5 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" fill="currentColor" />
      </svg>
      <span className="text-sm">{label}</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function StoryAnalyzerPage() {
  const [search, setSearch] = useState("");
  const [options, setOptions] = useState<StoryOption[]>([]);
  const [storyNumber, setStoryNumber] = useState("");
  const [refresh, setRefresh] = useState(true);
  const [rerun, setRerun] = useState(false);
  // Deep analysis (only when ticked): parent Feature/Epic, attachments, uploaded documents.
  const [deep, setDeep] = useState(false);
  const [includeParents, setIncludeParents] = useState(true);
  const [includeAttachments, setIncludeAttachments] = useState(true);
  const [files, setFiles] = useState<File[]>([]);
  const [fileError, setFileError] = useState("");
  const [instructions, setInstructions] = useState<Instructions | null>(null);
  const [showInstructions, setShowInstructions] = useState(false);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    fetch("/api/story-analyzer/instructions")
      .then((res) => (res.ok ? res.json() : null))
      .then(setInstructions)
      .catch(() => setInstructions(null));
    return () => abortRef.current?.abort();
  }, []);

  // Story list for the picker (server-side search, debounced).
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      fetch(`/api/story-analyzer/stories${search.trim() ? `?search=${encodeURIComponent(search.trim())}` : ""}`, { signal: controller.signal })
        .then((res) => (res.ok ? res.json() : { stories: [] }))
        .then((data) => setOptions(data.stories))
        .catch(() => undefined);
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [search, analysis]);

  const analyze = useCallback(
    async (id: number) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setRunning(true);
      setError("");
      setAnalysis(null);
      setStoryNumber(String(id));
      try {
        let res: Response;
        if (deep) {
          const form = new FormData();
          form.append("work_item_id", String(id));
          form.append("refresh", String(refresh));
          form.append("use_cache", String(!rerun));
          form.append("include_parents", String(includeParents));
          form.append("include_attachments", String(includeAttachments));
          files.forEach((file) => form.append("files", file, file.name));
          res = await fetch("/api/story-analyzer/analyze/deep", { method: "POST", body: form, signal: controller.signal });
        } else {
          res = await fetch("/api/story-analyzer/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ work_item_id: id, refresh, use_cache: !rerun }),
            signal: controller.signal,
          });
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail ?? `${res.status} ${res.statusText}`);
        if (abortRef.current === controller) setAnalysis(data as Analysis);
      } catch (err) {
        if (!isAbortError(err)) setError((err as Error).message);
      } finally {
        if (abortRef.current === controller) setRunning(false);
      }
    },
    [refresh, rerun, deep, includeParents, includeAttachments, files],
  );

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    const problems: string[] = [];
    const next = [...files];
    for (const file of Array.from(list)) {
      const problem = uploadProblem(file);
      if (problem) problems.push(problem);
      else if (next.some((existing) => existing.name === file.name && existing.size === file.size)) continue;
      else if (next.length >= MAX_UPLOADS) problems.push(`${file.name}: at most ${MAX_UPLOADS} documents`);
      else next.push(file);
    }
    setFiles(next);
    setFileError(problems.join(" · "));
  };

  const cancel = () => {
    abortRef.current?.abort();
    setRunning(false);
  };

  const parsedNumber = Number(storyNumber.replace(/^#|^AB#/i, "").trim());
  const validNumber = Number.isInteger(parsedNumber) && parsedNumber > 0;

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-6 md:p-8">
      <Card>
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-400">Azure Boards + GitHub + LLM</p>
        <h1 className="mt-2 text-2xl font-semibold text-white">Story analyzer</h1>
        <p className="mt-2 max-w-3xl text-sm text-slate-300">
          Pick a story or type its number, then analyze it. The review covers the story's health, its tags, the time spent in each
          stage, the description, acceptance criteria, attached evidence and comments, and suggests changes to the story.
        </p>

        <div className="mt-5 grid gap-6 lg:grid-cols-[1fr_20rem]">
          <div className="min-w-0">
            <label className="text-xs text-slate-400" htmlFor="story-search">
              Find a stored story
            </label>
            <input
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 focus:border-indigo-500 focus:outline-none"
              id="story-search"
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by title or ID"
              value={search}
            />
            <ul aria-label="Stories" className="mt-2 max-h-64 divide-y divide-slate-800 overflow-auto rounded-lg border border-slate-800">
              {options.length === 0 ? (
                <li className="px-3 py-3 text-sm text-slate-500">
                  No stored stories match. Type a story number on the right to load it from Azure DevOps.
                </li>
              ) : (
                options.map((option) => (
                  <li key={option.id}>
                    <button
                      className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-slate-800/60 ${
                        String(option.id) === storyNumber ? "bg-indigo-500/10" : ""
                      }`}
                      onClick={() => setStoryNumber(String(option.id))}
                      onDoubleClick={() => analyze(option.id)}
                      type="button"
                    >
                      <span className="min-w-0">
                        <span className="font-mono text-xs text-slate-500">#{option.id}</span>{" "}
                        <span className="text-slate-100">{option.title}</span>
                        <span className="block text-xs text-slate-500">
                          {option.state ?? "—"}
                          {option.assignee ? ` · ${option.assignee}` : ""}
                        </span>
                      </span>
                      {option.last_analyzed && (
                        <span className="shrink-0 rounded-full border border-slate-700 px-2 py-0.5 text-[11px] text-slate-400" title={when(option.last_analyzed)}>
                          analyzed
                        </span>
                      )}
                    </button>
                  </li>
                ))
              )}
            </ul>
          </div>

          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (validNumber && !running) analyze(parsedNumber);
            }}
          >
            <label className="text-xs text-slate-400" htmlFor="story-number">
              Story number
            </label>
            <input
              className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 font-mono text-lg text-slate-100 focus:border-indigo-500 focus:outline-none"
              id="story-number"
              inputMode="numeric"
              onChange={(e) => setStoryNumber(e.target.value)}
              placeholder="e.g. 12345"
              value={storyNumber}
            />
            <label className="flex items-center gap-2 text-sm text-slate-300" title="Load the latest description, links, attachments and comments before analyzing">
              <input checked={refresh} className="h-4 w-4 accent-indigo-500" onChange={(e) => setRefresh(e.target.checked)} type="checkbox" />
              Load the latest from Azure DevOps
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-300" title="Results are reused while the story and instructions are unchanged">
              <input checked={rerun} className="h-4 w-4 accent-indigo-500" onChange={(e) => setRerun(e.target.checked)} type="checkbox" />
              Re-run even if nothing changed
            </label>
            <label className="flex items-start gap-2 text-sm text-slate-300" title="Slower: reads more and sends it to the LLM">
              <input checked={deep} className="mt-0.5 h-4 w-4 accent-indigo-500" onChange={(e) => setDeep(e.target.checked)} type="checkbox" />
              <span>
                Deep analysis
                <span className="block text-xs text-slate-500">Also read the parent Feature, attachments and your documents</span>
              </span>
            </label>
            {deep && (
              <fieldset className="space-y-2 rounded-lg border border-slate-700 p-3">
                <legend className="px-1 text-xs text-slate-400">Context to include</legend>
                <label className="flex items-center gap-2 text-sm text-slate-300">
                  <input checked={includeParents} className="h-4 w-4 accent-indigo-500" onChange={(e) => setIncludeParents(e.target.checked)} type="checkbox" />
                  Parent Feature / Epic
                </label>
                <label className="flex items-center gap-2 text-sm text-slate-300">
                  <input checked={includeAttachments} className="h-4 w-4 accent-indigo-500" onChange={(e) => setIncludeAttachments(e.target.checked)} type="checkbox" />
                  Attachments on the story and its parents
                </label>
                <div>
                  <label
                    className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-dashed border-slate-600 px-3 py-2 text-sm text-slate-200 hover:border-indigo-400"
                    htmlFor="story-documents"
                  >
                    ⤒ Upload documents
                  </label>
                  <input
                    accept={UPLOAD_ACCEPT}
                    className="sr-only"
                    id="story-documents"
                    multiple
                    onChange={(e) => {
                      addFiles(e.target.files);
                      e.target.value = "";
                    }}
                    type="file"
                  />
                  <p className="mt-1 text-xs text-slate-500">Word (.docx), PDF or text · up to {MAX_UPLOADS} files, 10 MB each · used for this analysis only, not stored</p>
                </div>
                {files.length > 0 && (
                  <ul aria-label="Documents to upload" className="space-y-1 text-xs">
                    {files.map((file) => (
                      <li className="flex items-center justify-between gap-2 rounded border border-slate-800 px-2 py-1" key={`${file.name}-${file.size}`}>
                        <span className="min-w-0 truncate text-slate-200" title={file.name}>
                          {file.name} <span className="text-slate-500">({fileSize(file.size)})</span>
                        </span>
                        <button
                          aria-label={`Remove ${file.name}`}
                          className="shrink-0 text-slate-400 hover:text-rose-300"
                          onClick={() => setFiles(files.filter((existing) => existing !== file))}
                          type="button"
                        >
                          ✕
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {fileError && <p className="text-xs text-rose-300">{fileError}</p>}
              </fieldset>
            )}
            {running ? (
              <button className="rounded-lg border border-slate-600 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-slate-800" onClick={cancel} type="button">
                Cancel
              </button>
            ) : (
              <button
                className="rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:opacity-50"
                disabled={!validNumber}
                type="submit"
              >
                Analyze
              </button>
            )}
            {instructions && (
              <p className="text-xs text-slate-500">
                Team instructions:{" "}
                {instructions.used ? (
                  <button className="text-sky-300 hover:underline" onClick={() => setShowInstructions(!showInstructions)} type="button">
                    {instructions.path} {showInstructions ? "▲" : "▼"}
                  </button>
                ) : (
                  <span className="text-amber-300">none ({instructions.path} {instructions.exists ? "is empty" : "not found"})</span>
                )}
              </p>
            )}
          </form>
        </div>
        {showInstructions && instructions?.used && (
          <pre className="mt-4 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-950 p-4 text-xs text-slate-300">
            {instructions.text}
          </pre>
        )}
      </Card>

      {running && (
        <Card>
          <Spinner
            label={`Analyzing story #${storyNumber}${deep ? " with its parent items and documents" : ""}… an LLM review can take up to a minute${deep ? " or two" : ""}.`}
          />
        </Card>
      )}
      {error && <p className="rounded-lg bg-red-900/40 px-4 py-3 text-sm text-red-300">{error}</p>}
      {analysis && <AnalysisView analysis={analysis} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result
// ---------------------------------------------------------------------------

function AnalysisView({ analysis }: { analysis: Analysis }) {
  const { story, health, lifecycle } = analysis;
  const colors = useMemo(() => stageColorMap(analysis.stages), [analysis.stages]);
  return (
    <div className="space-y-6">
      <Card>
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <p className="font-mono text-xs text-slate-500">
              {story.type} #{story.id}
            </p>
            <h2 className="mt-1 text-xl font-semibold text-white">
              {story.url ? (
                <a className="hover:underline" href={story.url} rel="noreferrer" target="_blank">
                  {story.title} ↗
                </a>
              ) : (
                story.title
              )}
            </h2>
            <p className="mt-1 text-sm text-slate-400">
              {story.state ?? "—"}
              {story.assignee ? ` · ${story.assignee}` : ""}
              {story.story_points != null ? ` · ${story.story_points} pts` : ""}
              {story.release_version ? ` · Release ${story.release_version}` : ""}
            </p>
            <p className="mt-4 max-w-3xl text-sm text-slate-200">{health.summary}</p>
            <p className="mt-3 text-xs text-slate-500">
              Analyzed {when(analysis.analyzed_at)} by {analysis.provider}
              {analysis.context ? " · deep analysis (parent items and documents)" : ""}
              {analysis.cached ? " · reused (nothing changed since)" : ""}
              {analysis.instructions.used ? ` · with team instructions (${analysis.instructions.path})` : ""}
            </p>
            {analysis.warnings.map((warning) => (
              <p className="mt-2 rounded-lg bg-amber-900/30 px-3 py-2 text-xs text-amber-200" key={warning}>
                {warning}
              </p>
            ))}
          </div>
          <div className="w-full shrink-0 rounded-xl border border-slate-800 bg-slate-950/50 p-5 lg:w-72">
            <p className="text-xs uppercase tracking-wide text-slate-400">Story health</p>
            <div className="mt-2 flex items-baseline gap-2">
              <span className="text-5xl font-semibold tabular-nums text-white">{health.score}</span>
              <span className="text-slate-500">/100</span>
            </div>
            <span className={`mt-2 inline-block rounded-full border px-3 py-1 text-sm font-medium ${LEVEL_STYLES[health.level]}`}>{health.level}</span>
            <div className="mt-4 space-y-3">
              <ScoreBar label="Content quality (60%)" score={health.content_score} />
              <ScoreBar label="Delivery health (40%)" score={health.delivery_score} />
            </div>
          </div>
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <h3 className="text-lg font-semibold text-white">Tags</h3>
          <div className="mt-3 flex flex-wrap gap-2">
            {story.tags.length ? (
              story.tags.map((tag) => (
                <span className="rounded-full border border-slate-600 px-3 py-1 text-sm text-slate-200" key={tag}>
                  {tag}
                </span>
              ))
            ) : (
              <span className="text-sm text-slate-500">No tags.</span>
            )}
          </div>
          {analysis.suggested_tags.filter((tag) => !story.tags.map((t) => t.toLowerCase()).includes(tag.toLowerCase())).length > 0 && (
            <>
              <p className="mt-4 text-xs uppercase tracking-wide text-slate-400">Suggested</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {analysis.suggested_tags
                  .filter((tag) => !story.tags.map((t) => t.toLowerCase()).includes(tag.toLowerCase()))
                  .map((tag) => (
                    <span className="rounded-full border border-dashed border-indigo-400/60 px-3 py-1 text-sm text-indigo-200" key={tag}>
                      + {tag}
                    </span>
                  ))}
              </div>
            </>
          )}
        </Card>
        <DeliveryCard analysis={analysis} />
      </div>

      <Card>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-lg font-semibold text-white">Timeline</h3>
          <p className="text-xs text-slate-400">
            Lead time {duration(lifecycle.lead_time_hours ?? lifecycle.age_hours)}
            {lifecycle.flow_efficiency != null ? ` · flow efficiency ${Math.round(lifecycle.flow_efficiency * 100)}%` : ""}
            {lifecycle.rework_count ? ` · rework ${lifecycle.rework_count}×` : ""}
          </p>
        </div>
        <Timeline colors={colors} segments={lifecycle.timeline} />
        <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-xs text-slate-300">
          {analysis.stages
            .filter((stage) => lifecycle.stage_hours[stage.name] != null)
            .map((stage) => (
              <span className="flex items-center gap-2" key={stage.name}>
                <span className={`h-2.5 w-2.5 rounded-sm ${colors[stage.name]}`} />
                {stage.label}
                <span className="font-mono tabular-nums text-slate-400">{duration(lifecycle.stage_hours[stage.name])}</span>
              </span>
            ))}
        </div>
        <ol className="mt-4 grid gap-1 text-xs sm:grid-cols-2">
          {lifecycle.timeline.map((segment, index) => (
            <li className="flex items-center gap-2" key={index}>
              <span className={`h-2.5 w-2.5 shrink-0 rounded-sm ${segment.stage ? colors[segment.stage] : KIND_COLORS.other}`} />
              <span className="w-36 shrink-0 text-slate-200">{segment.state}</span>
              <span className="text-slate-500">{when(segment.start)}</span>
              <span className="ml-auto font-mono tabular-nums text-slate-300">{duration(segment.hours)}</span>
            </li>
          ))}
        </ol>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        {SECTION_TITLES.map(([key, title]) => (
          <SectionCard analysis={analysis} key={key} name={key} title={title} />
        ))}
      </div>

      {analysis.context && <ContextCard context={analysis.context} />}

      <SuggestionsCard analysis={analysis} />
    </div>
  );
}

function ContextCard({ context }: { context: StoryContext }) {
  const review = context.review;
  return (
    <Card>
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-white">Context: parent items and documents</h3>
          <p className="mt-1 text-xs text-slate-500">Deep analysis. How well the story covers and agrees with its Feature and documents; not part of the health score.</p>
        </div>
        {review?.score != null && (
          <span className={`rounded-full px-2.5 py-0.5 text-sm font-medium text-slate-950 ${scoreColor(review.score)}`} title="Context alignment">
            {review.score}
          </span>
        )}
      </div>
      {review && (
        <>
          <p className="mt-3 text-sm text-slate-300">{review.assessment}</p>
          {review.gaps.length > 0 && (
            <div className="mt-3">
              <p className="text-xs uppercase tracking-wide text-slate-400">Missing from the story</p>
              <List items={review.gaps} tone="bad" />
            </div>
          )}
          {review.conflicts.length > 0 && (
            <div className="mt-3">
              <p className="text-xs uppercase tracking-wide text-slate-400">Conflicts</p>
              <List items={review.conflicts} tone="bad" />
            </div>
          )}
          {review.sources_used.length > 0 && <p className="mt-3 text-xs text-slate-500">Sources used: {review.sources_used.join(", ")}</p>}
        </>
      )}

      <div className="mt-5 grid gap-6 lg:grid-cols-2">
        <div className="min-w-0">
          <p className="text-xs uppercase tracking-wide text-slate-400">Parent items ({context.parents.length})</p>
          {context.parents.length === 0 ? (
            <p className="mt-2 text-sm text-slate-500">Not included or none found.</p>
          ) : (
            <ul className="mt-2 space-y-2 text-sm">
              {context.parents.map((parent) => (
                <li className="rounded-lg border border-slate-800 px-3 py-2" key={parent.id}>
                  <span className="font-mono text-xs text-slate-500">
                    {parent.type} #{parent.id}
                  </span>{" "}
                  {parent.url ? (
                    <a className="text-sky-300 hover:underline" href={parent.url} rel="noreferrer" target="_blank">
                      {parent.title}
                    </a>
                  ) : (
                    <span className="text-slate-100">{parent.title}</span>
                  )}
                  <span className="text-xs text-slate-500"> · {parent.state ?? "—"}</span>
                  {parent.description && <p className="mt-1 line-clamp-3 text-xs text-slate-400">{parent.description}</p>}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="min-w-0">
          <p className="text-xs uppercase tracking-wide text-slate-400">Documents ({context.documents.length})</p>
          {context.documents.length === 0 ? (
            <p className="mt-2 text-sm text-slate-500">No attachments or uploads.</p>
          ) : (
            <ul className="mt-2 space-y-1 text-sm">
              {context.documents.map((document, index) => (
                <li className="flex items-start justify-between gap-3 rounded border border-slate-800 px-3 py-1.5" key={`${document.source}-${document.name}-${index}`}>
                  <span className="min-w-0">
                    <span className="break-words text-slate-100">{document.name}</span>
                    <span className="block text-xs text-slate-500">{document.source}</span>
                  </span>
                  {document.used ? (
                    <span className="shrink-0 rounded-full border border-emerald-500/40 px-2 py-0.5 text-[11px] text-emerald-300">
                      read · {document.characters.toLocaleString()} chars
                    </span>
                  ) : (
                    <span className="max-w-[14rem] shrink-0 text-right text-[11px] text-amber-300">{document.skipped}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Card>
  );
}

function Timeline({ segments, colors }: { segments: Segment[]; colors: Record<string, string> }) {
  const shown = segments.filter((segment) => (segment.hours ?? 0) > 0);
  const total = shown.reduce((sum, segment) => sum + (segment.hours ?? 0), 0);
  if (!total) return <p className="mt-3 text-sm text-slate-500">No state history yet: sync Azure DevOps to see the timeline.</p>;
  return (
    <div aria-label="Time in each stage" className="mt-4 flex h-6 w-full overflow-hidden rounded-md" role="img">
      {shown.map((segment, index) => (
        <span
          className={`${segment.stage ? colors[segment.stage] : KIND_COLORS.other} border-r border-slate-950/40 last:border-r-0`}
          key={index}
          style={{ width: `${((segment.hours ?? 0) / total) * 100}%` }}
          title={`${segment.label ?? segment.state}: ${duration(segment.hours)}${segment.assignee ? ` · ${segment.assignee}` : ""}`}
        />
      ))}
    </div>
  );
}

function List({ items, tone }: { items: string[]; tone: "good" | "bad" }) {
  if (!items.length) return null;
  return (
    <ul className="mt-2 space-y-1 text-sm">
      {items.map((item) => (
        <li className="flex gap-2" key={item}>
          <span aria-hidden="true" className={tone === "good" ? "text-emerald-400" : "text-amber-400"}>
            {tone === "good" ? "✓" : "!"}
          </span>
          <span className="text-slate-200">{item}</span>
        </li>
      ))}
    </ul>
  );
}

function SectionCard({ analysis, name, title }: { analysis: Analysis; name: keyof Analysis["sections"]; title: string }) {
  const section = analysis.sections[name];
  const [open, setOpen] = useState(false);
  const { inputs, comments_insights: insights } = analysis;
  return (
    <Card>
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-lg font-semibold text-white">{title}</h3>
        <span className={`rounded-full px-2.5 py-0.5 text-sm font-medium text-slate-950 ${scoreColor(section.score)}`}>{section.score}</span>
      </div>
      <p className="mt-2 text-sm text-slate-300">{section.assessment}</p>
      <List items={section.strengths} tone="good" />
      <List items={section.issues} tone="bad" />

      {name === "evidence" && (
        <div className="mt-4 text-sm">
          <p className="text-xs uppercase tracking-wide text-slate-400">
            Attachments ({inputs.attachments.length}) · inline images {inputs.inline_images + inputs.comments.reduce((n, c) => n + c.images, 0)}
          </p>
          <ul className="mt-1 space-y-1">
            {inputs.attachments.map((attachment) => (
              <li key={attachment.url}>
                <a className="text-sky-300 hover:underline" href={attachment.url} rel="noreferrer" target="_blank">
                  {attachment.name}
                </a>
              </li>
            ))}
          </ul>
          {analysis.lifecycle.pull_requests.length > 0 && (
            <p className="mt-2 text-xs text-slate-400">
              PRs:{" "}
              {analysis.lifecycle.pull_requests.map((pr, index) => (
                <span key={pr.url}>
                  {index > 0 && ", "}
                  <a className="text-sky-300 hover:underline" href={pr.url} rel="noreferrer" target="_blank">
                    {pr.repository}#{pr.number}
                  </a>{" "}
                  ({pr.status})
                </span>
              ))}
            </p>
          )}
        </div>
      )}

      {name === "comments" && (
        <div className="mt-4 space-y-3 text-sm">
          {insights.key_points.length > 0 && (
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-400">Key points</p>
              <List items={insights.key_points} tone="good" />
            </div>
          )}
          {insights.unresolved_questions.length > 0 && (
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-400">Unanswered questions</p>
              <List items={insights.unresolved_questions} tone="bad" />
            </div>
          )}
        </div>
      )}

      {(name === "description" || name === "acceptance_criteria" || name === "comments") && (
        <div className="mt-4">
          <button className="text-xs text-sky-300 hover:underline" onClick={() => setOpen(!open)} type="button">
            {open ? "Hide" : "Show"} {name === "comments" ? `the ${inputs.comments.length} comments` : "the text on the story"}
          </button>
          {open &&
            (name === "comments" ? (
              <ol className="mt-2 max-h-80 space-y-2 overflow-auto text-xs">
                {inputs.comments.map((comment, index) => (
                  <li className="rounded-lg border border-slate-800 px-3 py-2" key={index}>
                    <span className="text-slate-400">
                      {comment.author ?? "—"} · {when(comment.created_at)}
                    </span>
                    <p className="mt-1 whitespace-pre-wrap text-slate-200">{comment.text}</p>
                  </li>
                ))}
              </ol>
            ) : (
              <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs text-slate-200">
                {(name === "description" ? inputs.description : inputs.acceptance_criteria) || "(empty)"}
              </pre>
            ))}
        </div>
      )}
    </Card>
  );
}

function DeliveryCard({ analysis }: { analysis: Analysis }) {
  const { delivery, lifecycle } = analysis;
  return (
    <Card>
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-lg font-semibold text-white">Delivery health</h3>
        <span className={`rounded-full px-2.5 py-0.5 text-sm font-medium text-slate-950 ${scoreColor(delivery.score)}`}>{delivery.score}</span>
      </div>
      <p className="mt-2 text-sm text-slate-300">
        {lifecycle.outcome === "released" ? "Released" : lifecycle.outcome === "closed_unreleased" ? "Closed without release" : "In progress"}
        {lifecycle.stage ? ` · current stage: ${lifecycle.stage}` : ""}. Computed from the board history, not by the LLM.
      </p>
      {delivery.penalties.length ? (
        <ul className="mt-2 space-y-1 text-sm">
          {delivery.penalties.map((penalty) => (
            <li className="flex justify-between gap-3" key={penalty.reason}>
              <span className="text-slate-200">
                <span className="text-amber-400">!</span> {penalty.reason}
              </span>
              <span className="font-mono text-xs text-rose-300">−{penalty.points}</span>
            </li>
          ))}
        </ul>
      ) : (
        <List items={["No rework, skipped stages or unmerged work."]} tone="good" />
      )}
      {delivery.note && <p className="mt-2 text-xs text-amber-200">{delivery.note}</p>}
      <p className="mt-3 text-xs text-slate-400">
        Developers: {lifecycle.developers.map((p) => p.name).join(", ") || "—"} · Testers: {lifecycle.testers.map((p) => p.name).join(", ") || "—"}
      </p>
    </Card>
  );
}

function SuggestionsCard({ analysis }: { analysis: Analysis }) {
  const order = { high: 0, medium: 1, low: 2 };
  const suggestions = [...analysis.suggestions].sort((a, b) => order[a.priority] - order[b.priority]);
  const [copied, setCopied] = useState(false);
  const copyCriteria = async () => {
    try {
      await navigator.clipboard.writeText(analysis.suggested_acceptance_criteria.join("\n"));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };
  return (
    <Card>
      <h3 className="text-lg font-semibold text-white">Suggested changes to the story</h3>
      {suggestions.length === 0 ? (
        <p className="mt-2 text-sm text-slate-400">No changes suggested.</p>
      ) : (
        <ol className="mt-3 space-y-3">
          {suggestions.map((suggestion, index) => (
            <li className="rounded-xl border border-slate-800 p-4" key={index}>
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded-full border px-2 py-0.5 text-xs ${PRIORITY_STYLES[suggestion.priority]}`}>{suggestion.priority}</span>
                <span className="text-xs uppercase tracking-wide text-slate-400">{suggestion.area}</span>
              </div>
              <p className="mt-2 text-sm text-slate-100">{suggestion.change}</p>
              {suggestion.example && (
                <p className="mt-2 rounded-lg bg-slate-950 px-3 py-2 font-mono text-xs text-slate-300">{suggestion.example}</p>
              )}
            </li>
          ))}
        </ol>
      )}
      {analysis.suggested_acceptance_criteria.length > 0 && (
        <div className="mt-5">
          <div className="flex items-center justify-between">
            <p className="text-xs uppercase tracking-wide text-slate-400">Suggested acceptance criteria</p>
            <button className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800" onClick={copyCriteria} type="button">
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm text-slate-200">
            {analysis.suggested_acceptance_criteria.map((criterion) => (
              <li key={criterion}>{criterion}</li>
            ))}
          </ol>
        </div>
      )}
    </Card>
  );
}

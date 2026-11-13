import { useRef, useState, type ReactNode } from "react";
import ExportHtmlButton from "./ExportHtmlButton";
import { MAX_UPLOADS, UPLOAD_ACCEPT, addUploads, fileSize } from "./uploads";

// ---------------------------------------------------------------------------
// API types (POST /api/story-analyzer/split)
// ---------------------------------------------------------------------------

interface SuggestedStory {
  title: string;
  description: string;
  acceptance_criteria: string[];
  story_points: number | null;
  tags: string[];
  dependencies: string[];
  notes: string;
}

interface SplitResult {
  provider: string;
  warnings: string[];
  summary: string;
  stories: SuggestedStory[];
  open_questions: string[];
  assumptions: string[];
  parent: { id: number; type: string; title: string; url: string | null } | null;
  documents: { name: string; used: boolean; skipped: string | null; characters: number }[];
  instructions: { story_rules: boolean; splitting_rules: boolean; splitting_path: string };
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

function csvCell(value: string): string {
  return /[",\n\r]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/** CSV for Azure Boards "Import Work Items" (one User Story per row). */
function storiesCsv(stories: SuggestedStory[]): string {
  const header = ["Work Item Type", "Title", "Description", "Acceptance Criteria", "Story Points", "Tags"];
  const rows = stories.map((story) =>
    [
      "User Story",
      story.title,
      story.description,
      story.acceptance_criteria.map((criterion) => `- ${criterion}`).join("\n"),
      story.story_points != null ? String(story.story_points) : "",
      story.tags.join("; "),
    ].map(csvCell),
  );
  return "﻿" + [header, ...rows].map((row) => row.join(",")).join("\r\n") + "\r\n";
}

function storiesMarkdown(result: SplitResult, stories: SuggestedStory[]): string {
  const lines = [`# Suggested stories`, "", result.summary, ""];
  if (result.parent) lines.push(`Parent: ${result.parent.type} #${result.parent.id} ${result.parent.title}`, "");
  stories.forEach((story, index) => {
    lines.push(`## ${index + 1}. ${story.title}${story.story_points != null ? ` (${story.story_points} pts)` : ""}`, "", story.description, "", "**Acceptance criteria**", "");
    story.acceptance_criteria.forEach((criterion) => lines.push(`- ${criterion}`));
    if (story.tags.length) lines.push("", `**Tags:** ${story.tags.join(", ")}`);
    if (story.dependencies.length) lines.push(`**Depends on:** ${story.dependencies.join(", ")}`);
    if (story.notes) lines.push(`**Notes:** ${story.notes}`);
    lines.push("");
  });
  if (result.open_questions.length) lines.push("## Open questions", "", ...result.open_questions.map((q) => `- ${q}`), "");
  if (result.assumptions.length) lines.push("## Assumptions", "", ...result.assumptions.map((a) => `- ${a}`), "");
  return lines.join("\n");
}

function download(name: string, content: string, type: string): void {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl shadow-black/20 ${className}`}>{children}</section>;
}

const inputClass = "rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 focus:border-indigo-500 focus:outline-none";

export default function StoryCreator() {
  const [requirement, setRequirement] = useState("");
  const [parentId, setParentId] = useState("");
  const [maxStories, setMaxStories] = useState(8);
  const [files, setFiles] = useState<File[]>([]);
  const [fileError, setFileError] = useState("");
  const [result, setResult] = useState<SplitResult | null>(null);
  // Indexes of suggested stories the user removed (left out of the exports).
  const [removed, setRemoved] = useState<Set<number>>(new Set());
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const resultRef = useRef<HTMLDivElement | null>(null);

  const parent = Number(parentId.replace(/^#|^AB#/i, "").trim());
  const validParent = !parentId.trim() || (Number.isInteger(parent) && parent > 0);
  const canSplit = (requirement.trim() || files.length > 0 || (parentId.trim() && validParent)) && validParent && !running;
  const kept = result ? result.stories.filter((_, index) => !removed.has(index)) : [];

  const split = async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setRunning(true);
    setError("");
    setResult(null);
    setRemoved(new Set());
    try {
      const form = new FormData();
      form.append("requirement", requirement);
      if (parentId.trim()) form.append("parent_id", String(parent));
      form.append("max_stories", String(maxStories));
      files.forEach((file) => form.append("files", file, file.name));
      const res = await fetch("/api/story-analyzer/split", { method: "POST", body: form, signal: controller.signal });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail ?? `${res.status} ${res.statusText}`);
      if (abortRef.current === controller) setResult(data as SplitResult);
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) setError((err as Error).message);
    } finally {
      if (abortRef.current === controller) setRunning(false);
    }
  };

  const copyMarkdown = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(storiesMarkdown(result, kept));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      download("suggested-stories.md", storiesMarkdown(result, kept), "text/markdown;charset=utf-8");
    }
  };

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-6 md:p-8">
      <div data-export="exclude">
        <Card>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-400">Requirement + LLM</p>
          <h1 className="mt-2 text-2xl font-semibold text-white">Create stories</h1>
          <p className="mt-2 max-w-3xl text-sm text-slate-300">
            Paste a requirement (and optionally upload documents or name the parent Feature). The LLM splits it into user stories
            you can deliver and test independently, each with a description, Given/When/Then acceptance criteria and an estimate,
            following your team's rules. Nothing is written to Azure DevOps: review the stories, then copy or download them.
          </p>
          <div className="mt-5 grid gap-6 lg:grid-cols-[1fr_20rem]">
            <label className="flex min-w-0 flex-col gap-1 text-xs text-slate-400" htmlFor="requirement-text">
              Requirement
              <textarea
                className={`${inputClass} min-h-[14rem] font-mono text-xs leading-relaxed`}
                id="requirement-text"
                onChange={(e) => setRequirement(e.target.value)}
                placeholder={"e.g.\n# Saved payment methods\nCustomers can save cards at checkout and reuse them…"}
                value={requirement}
              />
            </label>
            <div className="flex flex-col gap-3">
              <label className="flex flex-col gap-1 text-xs text-slate-400" htmlFor="parent-feature">
                Parent Feature (optional)
                <input className={`${inputClass} font-mono`} id="parent-feature" inputMode="numeric" onChange={(e) => setParentId(e.target.value)} placeholder="Feature number" value={parentId} />
                {!validParent && <span className="text-rose-300">Enter a work item number.</span>}
              </label>
              <label className="flex flex-col gap-1 text-xs text-slate-400" htmlFor="max-stories">
                At most
                <select className={inputClass} id="max-stories" onChange={(e) => setMaxStories(Number(e.target.value))} value={maxStories}>
                  {[3, 5, 8, 10, 12, 15].map((count) => (
                    <option key={count} value={count}>
                      {count} stories
                    </option>
                  ))}
                </select>
              </label>
              <div>
                <label
                  className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-dashed border-slate-600 px-3 py-2 text-sm text-slate-200 hover:border-indigo-400"
                  htmlFor="requirement-documents"
                >
                  ⤒ Upload documents
                </label>
                <input
                  accept={UPLOAD_ACCEPT}
                  className="sr-only"
                  id="requirement-documents"
                  multiple
                  onChange={(e) => {
                    const added = addUploads(files, e.target.files);
                    setFiles(added.files);
                    setFileError(added.problems.join(" · "));
                    e.target.value = "";
                  }}
                  type="file"
                />
                <p className="mt-1 text-xs text-slate-500">Word (.docx), PDF or text · up to {MAX_UPLOADS} files, 10 MB each · not stored</p>
              </div>
              {files.length > 0 && (
                <ul aria-label="Requirement documents" className="space-y-1 text-xs">
                  {files.map((file) => (
                    <li className="flex items-center justify-between gap-2 rounded border border-slate-800 px-2 py-1" key={`${file.name}-${file.size}`}>
                      <span className="min-w-0 truncate text-slate-200" title={file.name}>
                        {file.name} <span className="text-slate-500">({fileSize(file.size)})</span>
                      </span>
                      <button aria-label={`Remove ${file.name}`} className="text-slate-400 hover:text-rose-300" onClick={() => setFiles(files.filter((f) => f !== file))} type="button">
                        ✕
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {fileError && <p className="text-xs text-rose-300">{fileError}</p>}
              {running ? (
                <button className="rounded-lg border border-slate-600 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-slate-800" onClick={() => abortRef.current?.abort()} type="button">
                  Cancel
                </button>
              ) : (
                <button className="rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:opacity-50" disabled={!canSplit} onClick={split} type="button">
                  Split into stories
                </button>
              )}
            </div>
          </div>
        </Card>
      </div>

      {running && (
        <Card>
          <p className="text-sm text-slate-300" role="status">
            Splitting the requirement… an LLM can take a minute or two.
          </p>
        </Card>
      )}
      {error && <p className="rounded-lg bg-red-900/40 px-4 py-3 text-sm text-red-300">{error}</p>}

      {result && (
        <div className="space-y-6" ref={resultRef}>
          <Card>
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div className="min-w-0">
                <h2 className="text-xl font-semibold text-white">
                  {kept.length} suggested stor{kept.length === 1 ? "y" : "ies"}
                </h2>
                <p className="mt-2 max-w-3xl text-sm text-slate-200">{result.summary}</p>
                <p className="mt-2 text-xs text-slate-500">
                  By {result.provider}
                  {result.parent ? ` · for ${result.parent.type} #${result.parent.id} ${result.parent.title}` : ""}
                  {result.instructions.story_rules || result.instructions.splitting_rules ? " · with team rules" : ""}
                  {result.documents.some((d) => d.used) ? ` · from ${result.documents.filter((d) => d.used).map((d) => d.name).join(", ")}` : ""}
                </p>
                {result.warnings.map((warning) => (
                  <p className="mt-2 rounded-lg bg-amber-900/30 px-3 py-2 text-xs text-amber-200" key={warning}>
                    {warning}
                  </p>
                ))}
              </div>
              <div className="flex shrink-0 flex-wrap gap-2" data-export="exclude">
                <button
                  className="rounded-lg border border-slate-600 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-slate-800 disabled:opacity-50"
                  disabled={!kept.length}
                  onClick={() => download("suggested-stories.csv", storiesCsv(kept), "text/csv;charset=utf-8")}
                  title="CSV for Azure Boards: Boards → Work items → Import Work Items"
                  type="button"
                >
                  ⤓ CSV for Azure Boards
                </button>
                <button className="rounded-lg border border-slate-600 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-slate-800 disabled:opacity-50" disabled={!kept.length} onClick={copyMarkdown} type="button">
                  {copied ? "Copied" : "Copy Markdown"}
                </button>
                <ExportHtmlButton info={() => ({ page: "Suggested stories", details: [result.summary] })} target={() => resultRef.current} />
              </div>
            </div>
          </Card>

          {(result.open_questions.length > 0 || result.assumptions.length > 0) && (
            <div className="grid gap-6 lg:grid-cols-2">
              {result.open_questions.length > 0 && (
                <Card>
                  <h3 className="text-lg font-semibold text-white">Open questions</h3>
                  <p className="mt-1 text-xs text-slate-500">Answer these before the stories go to refinement.</p>
                  <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-amber-100">
                    {result.open_questions.map((question) => (
                      <li key={question}>{question}</li>
                    ))}
                  </ul>
                </Card>
              )}
              {result.assumptions.length > 0 && (
                <Card>
                  <h3 className="text-lg font-semibold text-white">Assumptions</h3>
                  <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-200">
                    {result.assumptions.map((assumption) => (
                      <li key={assumption}>{assumption}</li>
                    ))}
                  </ul>
                </Card>
              )}
            </div>
          )}

          <ol className="space-y-4">
            {result.stories.map((story, index) =>
              removed.has(index) ? null : (
                <li key={index}>
                  <Card>
                    <div className="flex items-start justify-between gap-3">
                      <h3 className="text-lg font-semibold text-white">
                        <span className="mr-2 font-mono text-sm text-slate-500">{index + 1}.</span>
                        {story.title}
                      </h3>
                      <div className="flex shrink-0 items-center gap-2">
                        {story.story_points != null && (
                          <span className="rounded-full border border-indigo-400/50 px-2.5 py-0.5 text-sm text-indigo-200">{story.story_points} pts</span>
                        )}
                        <button
                          aria-label={`Remove story ${story.title}`}
                          className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-400 hover:text-rose-300"
                          data-export="exclude"
                          onClick={() => setRemoved(new Set(removed).add(index))}
                          title="Leave this story out of the exports"
                          type="button"
                        >
                          Remove
                        </button>
                      </div>
                    </div>
                    <p className="mt-2 whitespace-pre-wrap text-sm text-slate-200">{story.description}</p>
                    <p className="mt-4 text-xs uppercase tracking-wide text-slate-400">Acceptance criteria</p>
                    <ul className="mt-2 space-y-1 text-sm">
                      {story.acceptance_criteria.map((criterion) => (
                        <li className="flex gap-2" key={criterion}>
                          <span aria-hidden="true" className="text-emerald-400">
                            ✓
                          </span>
                          <span className="text-slate-200">{criterion}</span>
                        </li>
                      ))}
                    </ul>
                    <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-xs text-slate-400">
                      {story.tags.length > 0 && (
                        <span className="flex flex-wrap items-center gap-1">
                          Tags:
                          {story.tags.map((tag) => (
                            <span className="rounded-full border border-slate-600 px-2 py-0.5 text-slate-200" key={tag}>
                              {tag}
                            </span>
                          ))}
                        </span>
                      )}
                      {story.dependencies.length > 0 && <span>Depends on: {story.dependencies.join(", ")}</span>}
                    </div>
                    {story.notes && <p className="mt-2 text-xs text-slate-500">{story.notes}</p>}
                  </Card>
                </li>
              ),
            )}
          </ol>
          {removed.size > 0 && (
            <p className="text-xs text-slate-500" data-export="exclude">
              {removed.size} stor{removed.size === 1 ? "y" : "ies"} removed from the exports.{" "}
              <button className="text-sky-300 hover:underline" onClick={() => setRemoved(new Set())} type="button">
                Restore
              </button>
            </p>
          )}
        </div>
      )}
    </div>
  );
}

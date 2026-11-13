/**
 * "Export HTML": saves what a page currently shows as one self-contained,
 * static .html file that opens anywhere (no server, no JavaScript).
 *
 * The file holds a copy of the visible page (numbers, charts as SVG, tables,
 * expanded rows) plus the app's CSS. It contains no scripts, no API calls and
 * no raw data behind the page: only what is on screen, at the moment of export.
 *
 * Controls become plain text: buttons keep their label, inputs and selects show
 * their current value, checkboxes show ☑/☐. Elements marked
 * `data-export="exclude"` (actions like Sync or Analyze), file inputs and
 * download links are left out.
 */

export interface ExportInfo {
  /** Page name shown in the file's title and heading, e.g. "Team Overview". */
  page: string;
  /** Filters in effect, e.g. ["Date range: Last 30 days", "Team: Payments"]. */
  details?: string[];
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]!);
}

/** All CSS rules the page uses (Tailwind build or Vite dev styles), as text. */
function collectCss(): string {
  const parts: string[] = [];
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      parts.push(Array.from(sheet.cssRules).map((rule) => rule.cssText).join("\n"));
    } catch {
      // Cross-origin stylesheet (e.g. a font CDN): not readable, skip it.
    }
  }
  return parts.join("\n");
}

function replaceWith(element: Element, tag: string, text?: string): HTMLElement {
  const replacement = document.createElement(tag);
  replacement.className = element.getAttribute("class") ?? "";
  if (text !== undefined) replacement.textContent = text;
  else while (element.firstChild) replacement.appendChild(element.firstChild);
  element.replaceWith(replacement);
  return replacement;
}

/** A static copy of ``source``: controls as text, actions removed. */
function staticCopy(source: HTMLElement): HTMLElement {
  const copy = source.cloneNode(true) as HTMLElement;

  // Values live on the original elements (the clone only has the initial attributes).
  const originals = Array.from(source.querySelectorAll("input, select, textarea"));
  const clones = Array.from(copy.querySelectorAll("input, select, textarea"));
  clones.forEach((clone, index) => {
    const original = originals[index] as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
    if (clone.closest('[data-export="exclude"]')) return;
    if (original instanceof HTMLSelectElement) {
      const label = original.selectedOptions[0]?.textContent?.trim() ?? "";
      replaceWith(clone, "span", label);
    } else if (original instanceof HTMLInputElement && (original.type === "checkbox" || original.type === "radio")) {
      replaceWith(clone, "span", original.checked ? "☑" : "☐");
    } else if (original instanceof HTMLInputElement && original.type === "file") {
      clone.remove();
    } else {
      const value = original.value.trim();
      // Empty inputs (unused grid filters, search boxes) carry nothing worth keeping.
      if (value) replaceWith(clone, "span", value);
      else replaceWith(clone, "span", "");
    }
  });

  copy.querySelectorAll('[data-export="exclude"], [hidden], a[download], script, noscript, template').forEach((node) => node.remove());
  // Relative links point at this server (API endpoints, in-app routes): drop the link, keep the text.
  copy.querySelectorAll("a[href]").forEach((link) => {
    const href = link.getAttribute("href") ?? "";
    if (!/^https?:\/\//i.test(href)) replaceWith(link, "span");
    else {
      link.setAttribute("target", "_blank");
      link.setAttribute("rel", "noreferrer");
    }
  });
  copy.querySelectorAll("button").forEach((button) => replaceWith(button, "span"));
  copy.querySelectorAll("canvas").forEach((canvas, index) => {
    const original = source.querySelectorAll("canvas")[index] as HTMLCanvasElement | undefined;
    const image = document.createElement("img");
    try {
      if (original) image.src = original.toDataURL("image/png");
    } catch {
      /* tainted canvas: leave the image empty */
    }
    canvas.replaceWith(image);
  });
  // Nothing in the copy should react or point back at the app.
  copy.querySelectorAll("*").forEach((element) => {
    for (const attribute of Array.from(element.attributes)) {
      if (/^on/i.test(attribute.name) || attribute.name === "contenteditable" || attribute.name === "tabindex") {
        element.removeAttribute(attribute.name);
      }
    }
  });
  return copy;
}

/** Build the standalone HTML document for ``root`` (exported for tests). */
export function buildStaticHtml(root: HTMLElement, info: ExportInfo, now: Date = new Date()): string {
  const copy = staticCopy(root);
  const stamp = now.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  const details = (info.details ?? []).filter(Boolean);
  const title = `${info.page} · Git Analysis`;
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="Git Analysis export">
<title>${escapeHtml(title)}</title>
<style>
${collectCss()}
/* Export: static snapshot */
body { margin: 0; background: #020617; }
.export-banner { font: 13px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; color: #cbd5e1; background: #0f172a; border-bottom: 1px solid #1e293b; padding: 12px max(16px, 2vw); display: flex; flex-wrap: wrap; gap: 4px 16px; align-items: baseline; }
.export-banner strong { color: #f8fafc; font-size: 15px; }
.export-banner span { color: #94a3b8; }
</style>
</head>
<body>
<header class="export-banner">
<strong>${escapeHtml(info.page)}</strong>
<span>Exported ${escapeHtml(stamp)} from Git Analysis</span>
${details.map((detail) => `<span>${escapeHtml(detail)}</span>`).join("\n")}
<span>Static snapshot: shows what was on screen; no live data.</span>
</header>
<div class="min-h-screen bg-slate-950 text-slate-100">
${copy.outerHTML}
</div>
</body>
</html>
`;
}

function fileName(page: string, now: Date): string {
  const slug = page.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "page";
  const pad = (value: number) => String(value).padStart(2, "0");
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
  return `git-analysis-${slug}-${stamp}.html`;
}

/** Download ``root`` as a static HTML file. */
export function exportPageAsHtml(root: HTMLElement, info: ExportInfo): void {
  const now = new Date();
  const blob = new Blob([buildStaticHtml(root, info, now)], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName(info.page, now);
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

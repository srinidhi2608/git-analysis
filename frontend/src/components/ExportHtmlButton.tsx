import { useState } from "react";
import { type ExportInfo, exportPageAsHtml } from "../exportHtml";

/** "Export HTML" button: saves the element from ``target`` as a static, self-contained page. */
export default function ExportHtmlButton({
  target,
  info,
  className = "",
}: {
  target: () => HTMLElement | null;
  info: () => ExportInfo;
  className?: string;
}) {
  const [error, setError] = useState("");
  return (
    <span className="inline-flex flex-col items-end" data-export="exclude">
      <button
        className={`rounded-lg border border-slate-600 px-4 py-2 text-sm font-medium text-slate-200 transition hover:bg-slate-800 ${className}`}
        onClick={() => {
          const root = target();
          if (!root) return;
          try {
            exportPageAsHtml(root, info());
            setError("");
          } catch (err) {
            setError(`Export failed: ${(err as Error).message}`);
          }
        }}
        title="Save what this page shows as a static HTML file (no data behind it, no scripts)"
        type="button"
      >
        ⤓ Export HTML
      </button>
      {error && <span className="mt-1 text-xs text-rose-300">{error}</span>}
    </span>
  );
}

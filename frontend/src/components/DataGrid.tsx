import { Fragment, useMemo, useState, type ReactNode } from "react";

/**
 * Table with per-column sorting and filtering, used by every grid in the app.
 *
 * - Click a header to sort ascending, again for descending, a third time to clear.
 * - Each column has a filter under its header:
 *   - "text"   (default): case-insensitive "contains"
 *   - "select": pick one value; multi-valued cells (tags, flags, stories) match if any value matches
 *   - "number": `>5`, `>=5`, `<5`, `<=5`, `=5`, `5`, or a range `5-10`
 *   - "none":   no filter
 * - Optional expandable rows (`renderExpanded`) and a "Show more" limit for long lists.
 */

export type FilterKind = "text" | "select" | "number" | "none";
type Primitive = string | number | boolean | null | undefined;

export interface Column<T> {
  key: string;
  header: ReactNode;
  /** Value used to sort and filter (and shown when there is no `render`). */
  value: (row: T) => Primitive;
  render?: (row: T) => ReactNode;
  filter?: FilterKind;
  /** For "select" filters on multi-valued cells: every value of the row. */
  filterValues?: (row: T) => string[];
  sortable?: boolean;
  /** Tailwind classes for the cell (and header). */
  className?: string;
  title?: string;
}

interface Sort {
  key: string;
  dir: "asc" | "desc";
}

const EMPTY = "(empty)";

function matchesNumber(value: Primitive, expression: string): boolean {
  const text = expression.replace(/\s+/g, "");
  if (!text) return true;
  if (value == null || value === "" || Number.isNaN(Number(value))) return false;
  const number = Number(value);
  const range = text.match(/^(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)$/);
  if (range) return number >= Number(range[1]) && number <= Number(range[2]);
  const comparison = text.match(/^(>=|<=|>|<|=)?(-?\d+(?:\.\d+)?)$/);
  if (!comparison) return true; // incomplete input: don't hide rows while typing
  const target = Number(comparison[2]);
  switch (comparison[1]) {
    case ">":
      return number > target;
    case ">=":
      return number >= target;
    case "<":
      return number < target;
    case "<=":
      return number <= target;
    default:
      return number === target;
  }
}

function headerText(header: ReactNode, fallback: string): string {
  return typeof header === "string" ? header : fallback;
}

function compare(a: Primitive, b: Primitive): number {
  const aEmpty = a == null || a === "";
  const bEmpty = b == null || b === "";
  if (aEmpty || bEmpty) return aEmpty === bEmpty ? 0 : aEmpty ? 1 : -1; // empty values last
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

export default function DataGrid<T>({
  rows,
  columns,
  rowKey,
  renderExpanded,
  onRowClick,
  initialSort,
  pageSize = 200,
  maxHeight = "42rem",
  minWidth,
  emptyMessage = "No rows.",
  label,
}: {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string | number;
  renderExpanded?: (row: T) => ReactNode;
  onRowClick?: (row: T) => void;
  initialSort?: Sort;
  pageSize?: number;
  maxHeight?: string;
  /** e.g. "64rem": the table scrolls sideways inside its container below this width. */
  minWidth?: string;
  emptyMessage?: string;
  /** Accessible name of the table. */
  label?: string;
}) {
  const [sort, setSort] = useState<Sort | null>(initialSort ?? null);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState<string | number | null>(null);
  const [limit, setLimit] = useState(pageSize);

  const selectOptions = useMemo(() => {
    const options: Record<string, string[]> = {};
    for (const column of columns) {
      if (column.filter !== "select") continue;
      const values = new Set<string>();
      for (const row of rows) {
        const cell = column.filterValues ? column.filterValues(row) : [column.value(row)];
        if (cell.length === 0) values.add(EMPTY);
        for (const value of cell) values.add(value == null || value === "" ? EMPTY : String(value));
      }
      options[column.key] = [...values].sort((a, b) => compare(a, b));
    }
    return options;
  }, [columns, rows]);

  const visible = useMemo(() => {
    const active = columns.filter((column) => (filters[column.key] ?? "").trim() !== "");
    let result = rows.filter((row) =>
      active.every((column) => {
        const wanted = filters[column.key].trim();
        const kind = column.filter ?? "text";
        if (kind === "number") return matchesNumber(column.value(row), wanted);
        if (kind === "select") {
          const cell = column.filterValues ? column.filterValues(row) : [column.value(row)];
          const values = cell.length === 0 ? [EMPTY] : cell.map((value) => (value == null || value === "" ? EMPTY : String(value)));
          return values.includes(wanted);
        }
        const value = column.value(row);
        return value != null && String(value).toLowerCase().includes(wanted.toLowerCase());
      }),
    );
    if (sort) {
      const column = columns.find((item) => item.key === sort.key);
      if (column) {
        result = [...result].sort((a, b) => {
          const order = compare(column.value(a), column.value(b));
          // Keep empty values last in both directions.
          const aEmpty = column.value(a) == null || column.value(a) === "";
          const bEmpty = column.value(b) == null || column.value(b) === "";
          if (aEmpty !== bEmpty) return aEmpty ? 1 : -1;
          return sort.dir === "asc" ? order : -order;
        });
      }
    }
    return result;
  }, [rows, columns, filters, sort]);

  const filtered = Object.values(filters).some((value) => value.trim() !== "");
  const toggleSort = (key: string) =>
    setSort((current) =>
      current?.key !== key ? { key, dir: "asc" } : current.dir === "asc" ? { key, dir: "desc" } : null,
    );
  const setFilter = (key: string, value: string) => {
    setFilters((current) => ({ ...current, [key]: value }));
    setLimit(pageSize);
  };

  const filterInput = "w-full min-w-[4.5rem] rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs font-normal normal-case tracking-normal text-slate-200 placeholder:text-slate-600 focus:border-indigo-500 focus:outline-none";
  const shown = visible.slice(0, limit);

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 pb-2 text-xs text-slate-400">
        <span>
          {visible.length === rows.length ? `${rows.length} rows` : `${visible.length} of ${rows.length} rows`}
          {sort ? ` · sorted by ${headerText(columns.find((c) => c.key === sort.key)?.header, sort.key)} (${sort.dir === "asc" ? "ascending" : "descending"})` : ""}
        </span>
        {(filtered || sort) && (
          <button
            className="rounded border border-slate-700 px-2 py-1 text-slate-300 hover:bg-slate-800"
            onClick={() => {
              setFilters({});
              setSort(initialSort ?? null);
            }}
            type="button"
          >
            Clear filters and sorting
          </button>
        )}
      </div>
      <div className="overflow-auto rounded-lg border border-slate-800" style={{ maxHeight }}>
        <table aria-label={label} className="w-full text-left text-sm" style={minWidth ? { minWidth } : undefined}>
          <thead className="sticky top-0 z-10 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              {columns.map((column) => {
                const sorted = sort?.key === column.key ? sort.dir : null;
                const sortable = column.sortable !== false;
                return (
                  <th
                    aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none"}
                    className={`px-3 pt-2 font-medium ${column.className ?? ""}`}
                    key={column.key}
                    scope="col"
                    title={column.title}
                  >
                    {sortable ? (
                      <button
                        className="inline-flex items-center gap-1 uppercase tracking-wide hover:text-slate-100"
                        onClick={() => toggleSort(column.key)}
                        type="button"
                      >
                        {column.header}
                        <span aria-hidden="true" className={sorted ? "text-indigo-300" : "text-slate-600"}>
                          {sorted === "asc" ? "▲" : sorted === "desc" ? "▼" : "↕"}
                        </span>
                      </button>
                    ) : (
                      column.header
                    )}
                  </th>
                );
              })}
            </tr>
            <tr>
              {columns.map((column) => {
                const kind = column.filter ?? "text";
                const value = filters[column.key] ?? "";
                const name = `Filter ${headerText(column.header, column.key)}`;
                return (
                  <th className={`px-3 pb-2 pt-1 ${column.className ?? ""}`} key={column.key}>
                    {kind === "none" ? null : kind === "select" ? (
                      <select aria-label={name} className={filterInput} onChange={(e) => setFilter(column.key, e.target.value)} value={value}>
                        <option value="">All</option>
                        {(selectOptions[column.key] ?? []).map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        aria-label={name}
                        className={filterInput}
                        onChange={(e) => setFilter(column.key, e.target.value)}
                        placeholder={kind === "number" ? ">5, 1-10" : "Filter"}
                        title={kind === "number" ? "Number filter: >5, >=5, <5, <=5, =5 or a range 5-10" : "Contains (case-insensitive)"}
                        value={value}
                      />
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 text-slate-300">
            {shown.length === 0 ? (
              <tr>
                <td className="px-3 py-6 text-sm text-slate-500" colSpan={columns.length}>
                  {rows.length === 0 ? emptyMessage : "No rows match the filters."}
                </td>
              </tr>
            ) : (
              shown.map((row) => {
                const key = rowKey(row);
                const clickable = Boolean(renderExpanded || onRowClick);
                return (
                  <Fragment key={key}>
                    <tr
                      className={clickable ? "cursor-pointer hover:bg-slate-800/40" : undefined}
                      onClick={
                        clickable
                          ? () => {
                              if (renderExpanded) setExpanded(expanded === key ? null : key);
                              onRowClick?.(row);
                            }
                          : undefined
                      }
                    >
                      {columns.map((column) => (
                        <td className={`px-3 py-2.5 align-top ${column.className ?? ""}`} key={column.key}>
                          {column.render ? column.render(row) : (column.value(row) ?? "—")}
                        </td>
                      ))}
                    </tr>
                    {renderExpanded && expanded === key && (
                      <tr className="bg-slate-950/60">
                        <td className="px-4 py-4" colSpan={columns.length}>
                          {renderExpanded(row)}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      {visible.length > shown.length && (
        <button
          className="mt-2 rounded border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:bg-slate-800"
          onClick={() => setLimit(limit + pageSize)}
          type="button"
        >
          Show {Math.min(pageSize, visible.length - shown.length)} more ({visible.length - shown.length} hidden)
        </button>
      )}
    </div>
  );
}

/**
 * Date-time range shared by the dashboard, the DevOps page and both syncs.
 *
 * Presets are stored as rolling ranges ("last 30 days" keeps moving with the
 * clock); a custom range is fixed. Times are picked in the browser's local
 * time zone and sent to the API as UTC ISO strings.
 */
export type RangePreset = "24h" | "7d" | "14d" | "30d" | "90d" | "this-month" | "last-month" | "custom";

export interface DateRange {
  preset: RangePreset;
  /** Local date-time "YYYY-MM-DDTHH:mm" (used when preset is "custom"). */
  from: string;
  to: string;
}

export const PRESETS: { key: Exclude<RangePreset, "custom">; label: string }[] = [
  { key: "24h", label: "Last 24 hours" },
  { key: "7d", label: "Last 7 days" },
  { key: "14d", label: "Last 14 days" },
  { key: "30d", label: "Last 30 days" },
  { key: "90d", label: "Last 90 days" },
  { key: "this-month", label: "This month" },
  { key: "last-month", label: "Last month" },
];

const pad = (value: number) => String(value).padStart(2, "0");

/** Date -> "YYYY-MM-DDTHH:mm" in local time (the value of a datetime-local input). */
export function toLocalInput(date: Date): string {
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** Resolves a range to concrete start/end instants (end exclusive). */
export function resolveRange(range: DateRange, now: Date = new Date()): { start: Date; end: Date } {
  const daysBack = (days: number) => ({ start: new Date(now.getTime() - days * 86_400_000), end: now });
  switch (range.preset) {
    case "24h":
      return daysBack(1);
    case "7d":
      return daysBack(7);
    case "14d":
      return daysBack(14);
    case "30d":
      return daysBack(30);
    case "90d":
      return daysBack(90);
    case "this-month":
      return { start: new Date(now.getFullYear(), now.getMonth(), 1), end: now };
    case "last-month":
      return {
        start: new Date(now.getFullYear(), now.getMonth() - 1, 1),
        end: new Date(now.getFullYear(), now.getMonth(), 1),
      };
    default: {
      const start = new Date(range.from);
      const end = new Date(range.to);
      return { start, end };
    }
  }
}

export function isValidRange(range: DateRange): boolean {
  const { start, end } = resolveRange(range);
  return !Number.isNaN(start.getTime()) && !Number.isNaN(end.getTime()) && start < end;
}

/** Ranges that end "now"; the server's clock is used for their end. */
const ROLLING: RangePreset[] = ["24h", "7d", "14d", "30d", "90d", "this-month"];

/**
 * Query parameters / request body fields for the API (UTC ISO). Rolling ranges
 * omit ``date_to`` so a browser clock that runs behind never cuts off the
 * newest data (the API defaults the end to its own "now").
 */
export function rangeToApi(range: DateRange, now?: Date): Record<string, string> {
  const { start, end } = resolveRange(range, now);
  if (ROLLING.includes(range.preset)) return { date_from: start.toISOString() };
  return { date_from: start.toISOString(), date_to: end.toISOString() };
}

export function rangeLabel(range: DateRange): string {
  const preset = PRESETS.find((item) => item.key === range.preset);
  if (preset) return preset.label;
  const { start, end } = resolveRange(range);
  const format = (date: Date) =>
    date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  return `${format(start)} – ${format(end)}`;
}

/** Short label for cards, e.g. "30d" or "Oct 1 – Oct 8". */
export function rangeShortLabel(range: DateRange): string {
  if (range.preset.endsWith("d") || range.preset === "24h") return range.preset;
  const { start, end } = resolveRange(range);
  const format = (date: Date) => date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return `${format(start)} – ${format(new Date(end.getTime() - 1))}`;
}

export function defaultRange(preset: Exclude<RangePreset, "custom"> = "30d"): DateRange {
  const { start, end } = resolveRange({ preset, from: "", to: "" });
  return { preset, from: toLocalInput(start), to: toLocalInput(end) };
}

export function readStoredRange(key: string, fallback: DateRange): DateRange {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) ?? "null") as DateRange | null;
    if (parsed && typeof parsed.preset === "string" && isValidRange(parsed)) return parsed;
  } catch {
    /* storage unavailable or invalid */
  }
  return fallback;
}

export function storeRange(key: string, range: DateRange): void {
  try {
    localStorage.setItem(key, JSON.stringify(range));
  } catch {
    /* storage unavailable: the range just isn't remembered */
  }
}

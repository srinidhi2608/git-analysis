/**
 * Colours of the story lifecycle stages (config.yaml `lifecycle.stages`), shared
 * by the Story Quality and Story Analyzer pages so a stage looks the same everywhere.
 */
export type StageKind = "wait" | "work" | "released" | "closed" | "other";

export interface StageInfo {
  name: string;
  kind: StageKind;
  order: number;
}

export const KIND_COLORS: Record<StageKind, string> = {
  wait: "bg-slate-600",
  work: "bg-sky-500",
  released: "bg-emerald-500",
  closed: "bg-slate-400",
  other: "bg-rose-500",
};

// Distinct colours for the testing work stages, by position after development.
const TEST_COLORS = ["bg-amber-500", "bg-violet-500", "bg-fuchsia-500", "bg-orange-500"];

/** Tailwind background class per stage name. */
export function stageColorMap(stages: StageInfo[]): Record<string, string> {
  const colors: Record<string, string> = {};
  const devOrder = stages.find((stage) => stage.kind === "work")?.order ?? -1;
  let testIndex = 0;
  for (const stage of stages) {
    if (stage.kind === "work" && stage.order > devOrder) colors[stage.name] = TEST_COLORS[testIndex++ % TEST_COLORS.length];
    else colors[stage.name] = KIND_COLORS[stage.kind];
  }
  return colors;
}

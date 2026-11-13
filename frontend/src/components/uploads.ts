/** Documents users can upload to the Story analyzer (the server checks the same limits). */
export const UPLOAD_ACCEPT = ".docx,.pdf,.txt,.md,.csv,.log,.json,.xml,.yaml,.yml";
export const MAX_UPLOADS = 5;
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

export function uploadProblem(file: File): string | null {
  const extension = file.name.includes(".") ? `.${file.name.split(".").pop()!.toLowerCase()}` : "";
  if (extension === ".doc") return `${file.name}: old Word format; save it as .docx`;
  if (!UPLOAD_ACCEPT.split(",").includes(extension)) return `${file.name}: only Word (.docx), PDF and text files can be used`;
  if (file.size > MAX_UPLOAD_BYTES) return `${file.name}: larger than 10 MB`;
  return null;
}

export function fileSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Add ``list`` to ``current`` (skipping invalid files and duplicates); returns the files and problems. */
export function addUploads(current: File[], list: FileList | null): { files: File[]; problems: string[] } {
  const files = [...current];
  const problems: string[] = [];
  for (const file of Array.from(list ?? [])) {
    const problem = uploadProblem(file);
    if (problem) problems.push(problem);
    else if (files.some((existing) => existing.name === file.name && existing.size === file.size)) continue;
    else if (files.length >= MAX_UPLOADS) problems.push(`${file.name}: at most ${MAX_UPLOADS} documents`);
    else files.push(file);
  }
  return { files, problems };
}

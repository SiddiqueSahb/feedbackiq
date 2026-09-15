import { MAX_UPLOAD_BYTES } from "../api/imports";
import type { ImportSummary } from "../api/types";
import type { BadgeTone } from "../components/Badge";

const numberFormat = new Intl.NumberFormat("en-GB");

const dateTimeFormat = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatCount(value: number): string {
  return numberFormat.format(value);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : dateTimeFormat.format(date);
}

/** "1 row", "2 rows". */
export function rows(count: number): string {
  return `${formatCount(count)} ${count === 1 ? "row" : "rows"}`;
}

type AnalysisStatus = NonNullable<ImportSummary["analysis_status"]>;

/** How each analysis status reads in the product. */
export const ANALYSIS_STATUS: Record<AnalysisStatus, { label: string; tone: BadgeTone }> = {
  queued: { label: "Queued", tone: "neutral" },
  running: { label: "Analysing", tone: "info" },
  succeeded: { label: "Analysed", tone: "success" },
  failed: { label: "Analysis failed", tone: "danger" },
};

/**
 * Why a chosen file cannot be uploaded, or null if it can.
 *
 * A courtesy for the person choosing the file, not a security check: the API validates every
 * upload itself and its answer is what counts.
 */
export function fileProblem(file: File): string | null {
  const looksLikeCsv = file.name.toLowerCase().endsWith(".csv") || file.type === "text/csv";

  if (!looksLikeCsv) {
    return "Choose a CSV file (.csv).";
  }

  if (file.size === 0) {
    return "That file is empty.";
  }

  if (file.size > MAX_UPLOAD_BYTES) {
    return `That file is ${formatBytes(file.size)}. Files can be up to ${formatBytes(MAX_UPLOAD_BYTES)}.`;
  }

  return null;
}

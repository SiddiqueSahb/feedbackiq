import type { AnalyticsSummary } from "../api/types";
import { StatTile } from "../components/StatTile";
import { formatCompact, formatPercent, formatRating } from "./format";
import styles from "./SummaryTiles.module.css";

/**
 * The headline figures. Shares are of *analysed* feedback, as the API computes them, so they stay
 * honest while part of an upload is still waiting; with nothing analysed they show a dash rather
 * than a misleading 0%.
 */
export function SummaryTiles({ summary }: { summary: AnalyticsSummary }) {
  const analysed = summary.analysed;
  const hasAnalysis = analysed > 0;
  // Only say "waiting" when analysis is actually coming; failed analysis is not waited for.
  const noShare = { value: "—", caption: summary.analysis_pending > 0 ? "Waiting for analysis" : "Not analysed" };

  const share = (percentage: number, count: number) =>
    hasAnalysis
      ? { value: formatPercent(percentage), caption: `${formatCompact(count)} of ${formatCompact(analysed)} analysed` }
      : noShare;

  const negative = share(summary.sentiment_percentages.negative, summary.sentiment_counts.negative);
  const positive = share(summary.sentiment_percentages.positive, summary.sentiment_counts.positive);
  const unclassified = hasAnalysis
    ? { value: formatPercent(summary.unclassified_percentage), caption: "Complaints matching no category" }
    : noShare;

  return (
    <dl className={styles.tiles}>
      <StatTile label="Feedback" value={formatCompact(summary.total_feedback)} caption={feedbackCaption(summary)} />
      <StatTile label="Negative" value={negative.value} caption={negative.caption} />
      <StatTile label="Positive" value={positive.value} caption={positive.caption} />
      <StatTile label="Unclassified" value={unclassified.value} caption={unclassified.caption} />
      <StatTile
        label="Average rating"
        value={summary.average_rating == null ? "—" : formatRating(summary.average_rating)}
        caption={summary.average_rating == null ? "No ratings in this feedback" : "Out of 5"}
      />
    </dl>
  );
}

/** "All analysed", or what the unanalysed feedback is doing: "40 analysed · 2 waiting · 3 failed". */
function feedbackCaption(summary: AnalyticsSummary): string {
  if (summary.not_analysed === 0) {
    return "All analysed";
  }

  const parts = [`${formatCompact(summary.analysed)} analysed`];
  const unaccounted = summary.not_analysed - summary.analysis_pending - summary.analysis_failed;

  if (summary.analysis_pending > 0) parts.push(`${formatCompact(summary.analysis_pending)} waiting`);
  if (summary.analysis_failed > 0) parts.push(`${formatCompact(summary.analysis_failed)} failed`);
  // Feedback no analysis job was ever queued for: neither waiting nor failed.
  if (unaccounted > 0) parts.push(`${formatCompact(unaccounted)} not analysed`);

  return parts.join(" · ");
}

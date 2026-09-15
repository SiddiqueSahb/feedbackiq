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

  const share = (percentage: number, count: number) =>
    hasAnalysis
      ? { value: formatPercent(percentage), caption: `${formatCompact(count)} of ${formatCompact(analysed)} analysed` }
      : { value: "—", caption: "Waiting for analysis" };

  const negative = share(summary.sentiment_percentages.negative, summary.sentiment_counts.negative);
  const positive = share(summary.sentiment_percentages.positive, summary.sentiment_counts.positive);
  const unclassified = hasAnalysis
    ? { value: formatPercent(summary.unclassified_percentage), caption: "Complaints matching no category" }
    : { value: "—", caption: "Waiting for analysis" };

  return (
    <dl className={styles.tiles}>
      <StatTile
        label="Feedback"
        value={formatCompact(summary.total_feedback)}
        caption={
          summary.not_analysed > 0
            ? `${formatCompact(analysed)} analysed · ${formatCompact(summary.not_analysed)} waiting`
            : "All analysed"
        }
      />
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

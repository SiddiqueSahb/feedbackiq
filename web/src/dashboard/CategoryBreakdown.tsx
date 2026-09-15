import type { CategoryStat } from "../api/types";
import { formatCompact, formatPercent } from "./format";
import styles from "./CategoryBreakdown.module.css";

/**
 * Analysed feedback per complaint category, largest first, as the API ordered it.
 *
 * One series across unordered categories, so every bar is the same colour and there is no legend:
 * the length carries the comparison and the text beside each bar carries the exact value. The
 * results with no category are set apart, because they mean something different - see the note.
 */
export function CategoryBreakdown({ rows }: { rows: CategoryStat[] }) {
  const analysedTotal = rows.reduce((sum, row) => sum + row.count, 0);

  if (analysedTotal === 0) {
    return <p className={styles.empty}>No analysed feedback to break down yet.</p>;
  }

  const categorised = rows.filter((row) => row.category_key !== null);
  const uncategorised = rows.find((row) => row.category_key === null);
  const longest = Math.max(...rows.map((row) => row.count));

  return (
    <div className={styles.breakdown}>
      {categorised.length > 0 ? (
        <ul className={styles.list} aria-label="Complaint categories">
          {categorised.map((row) => (
            <Row key={row.category_key} name={row.category} row={row} longest={longest} />
          ))}
        </ul>
      ) : (
        <p className={styles.empty}>No feedback has been matched to a complaint category yet.</p>
      )}

      {uncategorised && uncategorised.count > 0 && (
        <div className={styles.uncategorised}>
          <ul className={styles.list} aria-label="Not categorised">
            <Row name="Not categorised" row={uncategorised} longest={longest} muted />
          </ul>
          <p className={styles.note}>
            Positive feedback is not given a complaint category, and complaints that match none of
            the categories are counted here too - a rising share can be an emerging issue.
          </p>
        </div>
      )}
    </div>
  );
}

function Row({ name, row, longest, muted = false }: { name: string; row: CategoryStat; longest: number; muted?: boolean }) {
  const width = longest > 0 ? (row.count / longest) * 100 : 0;

  return (
    <li className={styles.row}>
      <span className={styles.name} title={name}>
        {name}
      </span>
      <span className={styles.track} aria-hidden="true">
        <span
          className={[styles.bar, muted && styles.muted].filter(Boolean).join(" ")}
          // A non-zero count always shows a sliver, so it never reads as "none".
          style={{ width: `${row.count > 0 ? Math.max(width, 0.75) : 0}%` }}
        />
      </span>
      <span className={styles.value}>
        {formatCompact(row.count)}
        <span className={styles.share}>{formatPercent(row.percentage)}</span>
      </span>
    </li>
  );
}

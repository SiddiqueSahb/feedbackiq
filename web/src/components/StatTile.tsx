import type { ReactNode } from "react";

import styles from "./StatTile.module.css";

interface StatTileProps {
  /** Sentence case, no trailing colon. */
  label: string;
  value: string;
  /** One short line of context under the value: what it is a share of, or why it is empty. */
  caption?: ReactNode;
}

/**
 * One headline figure. A number, not a chart: a single value reads faster as text than as a
 * one-bar chart.
 */
export function StatTile({ label, value, caption }: StatTileProps) {
  return (
    <div className={styles.tile}>
      <dt className={styles.label}>{label}</dt>
      <dd className={styles.value}>{value}</dd>
      {caption && <dd className={styles.caption}>{caption}</dd>}
    </div>
  );
}

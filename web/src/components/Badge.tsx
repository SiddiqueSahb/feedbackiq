import type { ReactNode } from "react";

import styles from "./Badge.module.css";

export type BadgeTone = "neutral" | "info" | "success" | "danger";

/** A short status label. The colour supports the text; the text always carries the meaning. */
export function Badge({ tone = "neutral", children }: { tone?: BadgeTone; children: ReactNode }) {
  return (
    <span className={`${styles.badge} ${styles[tone] ?? ""}`}>
      <span className={styles.dot} aria-hidden="true" />
      {children}
    </span>
  );
}

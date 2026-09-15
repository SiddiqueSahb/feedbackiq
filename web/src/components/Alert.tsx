import type { ReactNode } from "react";

import styles from "./Alert.module.css";

interface AlertProps {
  tone?: "error" | "info" | "success";
  title?: string;
  children: ReactNode;
}

/**
 * A message block. Errors use `role="alert"` so they are announced as soon as they appear;
 * other tones use `role="status"`, which is announced politely.
 */
export function Alert({ tone = "info", title, children }: AlertProps) {
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`${styles.alert} ${styles[tone] ?? ""}`}>
      {title && <p className={styles.title}>{title}</p>}
      <div className={styles.body}>{children}</div>
    </div>
  );
}

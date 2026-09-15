import type { ReactNode } from "react";

import { Icon, type IconName } from "./Icon";
import styles from "./EmptyState.module.css";

interface EmptyStateProps {
  icon?: IconName;
  title: string;
  children?: ReactNode;
  /** The next step, e.g. a link to upload feedback. An empty state should always offer one. */
  action?: ReactNode;
}

export function EmptyState({ icon, title, children, action }: EmptyStateProps) {
  return (
    <div className={styles.empty}>
      {icon && (
        <span className={styles.icon}>
          <Icon name={icon} size={20} />
        </span>
      )}
      <h2 className={styles.title}>{title}</h2>
      {children && <p className={styles.text}>{children}</p>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  );
}

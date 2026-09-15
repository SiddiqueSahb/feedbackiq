import type { ReactNode } from "react";

import styles from "./Card.module.css";

interface CardProps {
  title?: string;
  /** Right-aligned content in the header, e.g. a secondary action. */
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** A bordered surface for one block of content, with an optional header. */
export function Card({ title, actions, children, className }: CardProps) {
  return (
    <section className={[styles.card, className].filter(Boolean).join(" ")}>
      {(title || actions) && (
        <header className={styles.header}>
          {title && <h2 className={styles.title}>{title}</h2>}
          {actions && <div className={styles.actions}>{actions}</div>}
        </header>
      )}
      <div className={styles.body}>{children}</div>
    </section>
  );
}

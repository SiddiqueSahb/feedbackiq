import type { ReactNode } from "react";

import { Logo } from "../components/Logo";
import styles from "./AuthLayout.module.css";

interface AuthLayoutProps {
  title: string;
  subtitle?: string;
  children: ReactNode;
  /** Below the card, e.g. the link between sign-in and registration. */
  footer?: ReactNode;
}

/** The frame for signing in and registering: the brand, one card, one way out. */
export function AuthLayout({ title, subtitle, children, footer }: AuthLayoutProps) {
  return (
    <div className={styles.page}>
      <header className={styles.top}>
        <Logo />
      </header>

      <main className={styles.main}>
        <div className={styles.card}>
          <div className={styles.heading}>
            <h1 className={styles.title}>{title}</h1>
            {subtitle && <p className={styles.subtitle}>{subtitle}</p>}
          </div>
          {children}
        </div>
        {footer && <p className={styles.footer}>{footer}</p>}
      </main>
    </div>
  );
}

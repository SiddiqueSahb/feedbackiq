import styles from "./Logo.module.css";

/** The FeedbackIQ mark and wordmark. The mark matches public/favicon.svg. */
export function Logo() {
  return (
    <span className={styles.logo}>
      <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true" focusable="false">
        <rect width="32" height="32" rx="7" fill="#16181d" />
        <rect x="8" y="17" width="4" height="7" rx="1" fill="#ffffff" opacity="0.55" />
        <rect x="14" y="12" width="4" height="12" rx="1" fill="#ffffff" opacity="0.8" />
        <rect x="20" y="8" width="4" height="16" rx="1" fill="#ffffff" />
      </svg>
      <span className={styles.wordmark}>FeedbackIQ</span>
    </span>
  );
}

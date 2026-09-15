import { Link } from "react-router";

import styles from "./CentredPage.module.css";

export function NotFoundPage() {
  return (
    <main className={styles.page}>
      <div className={styles.panel}>
        <p className={styles.eyebrow}>404</p>
        <h1 className={styles.title}>Page not found</h1>
        <p className={styles.text}>The page you were looking for does not exist or has moved.</p>
        <Link to="/">Go to FeedbackIQ</Link>
      </div>
    </main>
  );
}

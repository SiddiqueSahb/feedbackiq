import styles from "./CentredPage.module.css";

/**
 * Temporary landing page for the scaffold (Milestone 8, Part 2). Replaced by the sign-in flow and
 * the dashboard in Part 3.
 */
export function WelcomePage() {
  return (
    <main className={styles.page}>
      <div className={styles.panel}>
        <p className={styles.eyebrow}>FeedbackIQ</p>
        <h1 className={styles.title}>Customer feedback intelligence</h1>
        <p className={styles.text}>The web app is being set up.</p>
      </div>
    </main>
  );
}

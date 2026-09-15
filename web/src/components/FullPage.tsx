import { Logo } from "./Logo";
import { Button } from "./Button";
import { Spinner } from "./Spinner";
import styles from "./FullPage.module.css";

export function FullPageLoading({ message }: { message: string }) {
  return (
    <div className={styles.page}>
      <div className={styles.panel} role="status">
        <Spinner />
        <p className={styles.message}>{message}</p>
      </div>
    </div>
  );
}

export function FullPageError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className={styles.page}>
      <div className={styles.panel}>
        <Logo />
        <div role="alert" className={styles.alert}>
          <p className={styles.title}>FeedbackIQ could not load</p>
          <p className={styles.message}>{message}</p>
        </div>
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      </div>
    </div>
  );
}

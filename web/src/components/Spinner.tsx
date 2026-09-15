import styles from "./Spinner.module.css";

interface SpinnerProps {
  size?: "small" | "medium";
}

/**
 * A decorative loading indicator. Hidden from screen readers: whatever is loading says so in
 * text (a button's label, a page's status message), so the spinner never has to.
 */
export function Spinner({ size = "medium" }: SpinnerProps) {
  return <span className={`${styles.spinner} ${styles[size] ?? ""}`} aria-hidden="true" />;
}

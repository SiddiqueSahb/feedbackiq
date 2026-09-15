import type { ButtonHTMLAttributes } from "react";

import styles from "./Button.module.css";
import { Spinner } from "./Spinner";

type Variant = "primary" | "secondary" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  /** Shows a spinner and disables the button, so a form cannot be submitted twice. */
  loading?: boolean;
  fullWidth?: boolean;
}

export function Button({
  variant = "primary",
  loading = false,
  fullWidth = false,
  disabled,
  type = "button",
  className,
  children,
  ...rest
}: ButtonProps) {
  const classes = [styles.button, styles[variant], fullWidth && styles.fullWidth, className]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      type={type}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && <Spinner size="small" />}
      <span>{children}</span>
    </button>
  );
}

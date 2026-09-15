import { useId, type InputHTMLAttributes } from "react";

import styles from "./TextField.module.css";

interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  /** Guidance shown under the field, e.g. the password rule. Replaced by `error` when present. */
  hint?: string;
  error?: string;
}

/**
 * A labelled input. The label, hint and error are wired to the input with `for` and
 * `aria-describedby`, so a screen reader announces the rule and the problem with the field.
 */
export function TextField({ label, hint, error, id, className, ...rest }: TextFieldProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;

  const hintId = hint && !error ? `${inputId}-hint` : undefined;
  const errorId = error ? `${inputId}-error` : undefined;

  return (
    <div className={[styles.field, className].filter(Boolean).join(" ")}>
      <label htmlFor={inputId} className={styles.label}>
        {label}
      </label>
      <input
        id={inputId}
        className={[styles.input, error && styles.invalid].filter(Boolean).join(" ")}
        aria-invalid={error ? true : undefined}
        aria-describedby={errorId ?? hintId}
        {...rest}
      />
      {hintId && (
        <p id={hintId} className={styles.hint}>
          {hint}
        </p>
      )}
      {errorId && (
        <p id={errorId} className={styles.error}>
          {error}
        </p>
      )}
    </div>
  );
}

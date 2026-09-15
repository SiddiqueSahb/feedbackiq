import { Link, type LinkProps } from "react-router";

import styles from "./Button.module.css";

interface ButtonLinkProps extends LinkProps {
  variant?: "primary" | "secondary" | "ghost";
}

/** Navigation that looks like a button - a link, so it opens in a new tab and reads as a link. */
export function ButtonLink({ variant = "primary", className, ...rest }: ButtonLinkProps) {
  return (
    <Link
      className={[styles.button, styles[variant], styles.link, className].filter(Boolean).join(" ")}
      {...rest}
    />
  );
}

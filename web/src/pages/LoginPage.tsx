import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { useDocumentTitle } from "../app/useDocumentTitle";
import { safeNextPath } from "../auth/nextPath";
import { useSignIn } from "../auth/session";
import { Alert } from "../components/Alert";
import { Button } from "../components/Button";
import { TextField } from "../components/TextField";
import styles from "./AuthForm.module.css";
import { AuthLayout } from "./AuthLayout";

export function LoginPage() {
  useDocumentTitle("Sign in");

  const [params] = useSearchParams();
  const navigate = useNavigate();
  const signIn = useSignIn();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const next = params.get("next");
  const registerLink = next ? `/register?next=${encodeURIComponent(next)}` : "/register";

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    signIn.mutate(
      { email, password },
      { onSuccess: () => navigate(safeNextPath(next), { replace: true }) },
    );
  }

  return (
    <AuthLayout
      title="Sign in to FeedbackIQ"
      subtitle="Welcome back. Enter your work email and password."
      footer={
        <>
          New to FeedbackIQ? <Link to={registerLink}>Create an account</Link>
        </>
      }
    >
      <form className={styles.form} onSubmit={handleSubmit}>
        {/* The API's own wording: one message for any wrong detail, so it never reveals which. */}
        {signIn.isError && <Alert tone="error">{signIn.error.message}</Alert>}

        <TextField
          label="Work email"
          type="email"
          name="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <TextField
          label="Password"
          type="password"
          name="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />

        <Button type="submit" fullWidth loading={signIn.isPending} className={styles.submit}>
          {signIn.isPending ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </AuthLayout>
  );
}

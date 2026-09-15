import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { useDocumentTitle } from "../app/useDocumentTitle";
import { safeNextPath } from "../auth/nextPath";
import { useRegister } from "../auth/session";
import { Alert } from "../components/Alert";
import { Button } from "../components/Button";
import { TextField } from "../components/TextField";
import styles from "./AuthForm.module.css";
import { AuthLayout } from "./AuthLayout";

/**
 * Creates an account and a new organisation owned by it.
 *
 * The password rule is shown up front but enforced only by the API, so there is exactly one
 * definition of it (auth/credentials.py); a refusal comes back with the rule that was broken.
 */
export function RegisterPage() {
  useDocumentTitle("Create an account");

  const [params] = useSearchParams();
  const navigate = useNavigate();
  const register = useRegister();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [organisationName, setOrganisationName] = useState("");

  const next = params.get("next");
  const loginLink = next ? `/login?next=${encodeURIComponent(next)}` : "/login";

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    register.mutate(
      { email, password, organisation_name: organisationName },
      { onSuccess: () => navigate(safeNextPath(next), { replace: true }) },
    );
  }

  return (
    <AuthLayout
      title="Create your FeedbackIQ account"
      subtitle="Set up your organisation to start analysing customer feedback."
      footer={
        <>
          Already have an account? <Link to={loginLink}>Sign in</Link>
        </>
      }
    >
      <form className={styles.form} onSubmit={handleSubmit}>
        {register.isError && <Alert tone="error">{register.error.message}</Alert>}

        <TextField
          label="Organisation name"
          name="organisation"
          autoComplete="organization"
          placeholder="Acme Ltd"
          required
          value={organisationName}
          onChange={(event) => setOrganisationName(event.target.value)}
        />
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
          autoComplete="new-password"
          hint="At least 12 characters. A passphrase works well."
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />

        <Button type="submit" fullWidth loading={register.isPending} className={styles.submit}>
          {register.isPending ? "Creating account…" : "Create account"}
        </Button>
      </form>
    </AuthLayout>
  );
}

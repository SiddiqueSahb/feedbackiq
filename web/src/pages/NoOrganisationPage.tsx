import { useDocumentTitle } from "../app/useDocumentTitle";
import { useSignOut } from "../auth/session";
import { Button } from "../components/Button";
import { Logo } from "../components/Logo";
import styles from "./CentredPage.module.css";

/**
 * Shown to a signed-in user who belongs to no organisation - for example after their membership
 * was removed. Every customer page would be refused by the API (403), so this explains why
 * instead of showing pages full of errors.
 */
export function NoOrganisationPage({ email }: { email: string }) {
  useDocumentTitle("No organisation");
  const { signOut, isPending, error } = useSignOut();

  return (
    <main className={styles.page}>
      <div className={styles.panel}>
        <Logo />
        <h1 className={styles.title}>You're not part of an organisation</h1>
        <p className={styles.text}>
          You're signed in as <strong>{email}</strong>, but this account doesn't belong to an
          organisation, so there is no feedback to show. Ask an owner of your organisation to add
          you, or sign out and use a different account.
        </p>
        {error && (
          <p role="alert" className={styles.error}>
            {error.message}
          </p>
        )}
        <Button variant="secondary" onClick={signOut} loading={isPending}>
          Sign out
        </Button>
      </div>
    </main>
  );
}

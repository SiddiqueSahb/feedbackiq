import { NavLink, Outlet } from "react-router";

import { useSignOut, useSignedInUser } from "../auth/session";
import { Icon, type IconName } from "../components/Icon";
import { Logo } from "../components/Logo";
import styles from "./AppShell.module.css";

const NAVIGATION: { to: string; label: string; icon: IconName }[] = [
  { to: "/dashboard", label: "Dashboard", icon: "dashboard" },
  { to: "/imports", label: "Imports", icon: "imports" },
];

const ROLE_LABELS: Record<string, string> = {
  owner: "Owner",
  member: "Member",
};

/**
 * The frame around every signed-in page: navigation, the organisation the person is acting for,
 * and their account. The organisation shown is the one the API reported for this session.
 */
export function AppShell() {
  const user = useSignedInUser();
  const { signOut, isPending, error } = useSignOut();

  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <Logo />
        </div>

        <div className={styles.organisation}>
          <span className={styles.organisationLabel}>Organisation</span>
          <span className={styles.organisationName}>{user.organisation.name}</span>
        </div>

        <nav aria-label="Main" className={styles.nav}>
          {NAVIGATION.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => [styles.navLink, isActive && styles.active].filter(Boolean).join(" ")}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className={styles.account}>
          <div className={styles.identity}>
            <span className={styles.email} title={user.email}>
              {user.email}
            </span>
            <span className={styles.role}>{ROLE_LABELS[user.organisation.role] ?? user.organisation.role}</span>
          </div>
          <button type="button" className={styles.signOut} onClick={signOut} disabled={isPending}>
            <Icon name="signOut" />
            <span>{isPending ? "Signing out…" : "Sign out"}</span>
          </button>
          {error && (
            <p role="alert" className={styles.signOutError}>
              {error.message}
            </p>
          )}
        </div>
      </aside>

      <main className={styles.main}>
        <div className={styles.content}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}

import { Navigate, Outlet, useLocation, useSearchParams } from "react-router";

import { FullPageError, FullPageLoading } from "../components/FullPage";
import { NoOrganisationPage } from "../pages/NoOrganisationPage";
import { safeNextPath } from "./nextPath";
import { useSession } from "./session";

/**
 * Renders the app only for a signed-in user with an organisation.
 *
 *   still asking the API        a loading screen
 *   the API could not answer    an error with a retry (not a sign-in page: the person may well be
 *                               signed in, and sending them to sign in would be misleading)
 *   not signed in               → /login?next=<where they were going>
 *   signed in, no organisation  an explanation, instead of pages that would all be refused (403)
 */
export function RequireAuth() {
  const session = useSession();
  const location = useLocation();

  if (session.isPending) {
    return <FullPageLoading message="Loading FeedbackIQ…" />;
  }

  if (session.isError) {
    return <FullPageError message={session.error.message} onRetry={() => void session.refetch()} />;
  }

  if (!session.data) {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }

  if (!session.data.organisation) {
    return <NoOrganisationPage email={session.data.email} />;
  }

  return <Outlet />;
}

/** Renders sign-in and registration only when nobody is signed in; otherwise moves on. */
export function PublicOnly() {
  const session = useSession();
  const [params] = useSearchParams();

  if (session.isPending) {
    return <FullPageLoading message="Loading…" />;
  }

  if (session.data) {
    return <Navigate to={safeNextPath(params.get("next"))} replace />;
  }

  // Signed out - or the API is unreachable, in which case the form still renders and submitting
  // it shows the connection error where the person is looking.
  return <Outlet />;
}

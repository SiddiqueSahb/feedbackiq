import type { QueryClient } from "@tanstack/react-query";

/**
 * The cache key holding the signed-in user: the answer from `GET /api/v1/auth/me`, or `null`.
 *
 * This is the app's only record of who is signed in. It holds what the API returned - an email,
 * an organisation name and role - never a token: the token lives in an HttpOnly cookie that
 * JavaScript cannot read.
 */
export const SESSION_KEY = ["session"] as const;

/**
 * Forget the previous user entirely: every cached response, then mark the app signed out.
 *
 * Clearing everything, not just the session, matters on a shared computer - one organisation's
 * feedback must not stay in memory for whoever signs in next.
 */
export function endSession(queryClient: QueryClient): void {
  queryClient.clear();
  queryClient.setQueryData(SESSION_KEY, null);
}

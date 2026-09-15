import type { QueryClient } from "@tanstack/react-query";

import type { CurrentUser } from "../api/types";

/**
 * The cache key holding the signed-in user: the answer from `GET /api/v1/auth/me`, or `null`.
 *
 * This is the app's only record of who is signed in. It holds what the API returned - an email,
 * an organisation name and role - never a token: the token lives in an HttpOnly cookie that
 * JavaScript cannot read.
 */
export const SESSION_KEY = ["session"] as const;

/**
 * Mark the app signed out and forget every cached response.
 *
 * Forgetting everything matters on a shared computer: one organisation's feedback must not stay in
 * memory for whoever signs in next.
 */
export function endSession(queryClient: QueryClient): void {
  replaceSession(queryClient, null);
}

/**
 * Record who is now signed in (or `null`), and drop everything cached for whoever was before.
 *
 * **The order matters.** The session is written first, onto the entry the route guards are already
 * watching, so they are told at once and re-render. Clearing the whole cache first would detach
 * them from that entry: the new value would land on a fresh entry nobody watches, and the app would
 * keep showing a page the server has just refused. (Found in Milestone 8: a 401 during an upload
 * left the imports page on screen.)
 *
 * Mutations are not cleared: this runs from inside mutation callbacks (sign-in, sign-out, a refused
 * upload), and their results live in component state that unmounts when the route changes.
 */
export function replaceSession(queryClient: QueryClient, user: CurrentUser | null): void {
  queryClient.setQueryData(SESSION_KEY, user);
  queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== SESSION_KEY[0] });
}

/**
 * The authentication endpoints (Milestone 7 contract, docs/production/milestone-07.md §16).
 *
 * These only move data. What a response *means* for the app - signed in, signed out, which
 * organisation - is decided by the API and applied in `auth/session.ts`.
 */
import { ApiError, apiRequest } from "./client";
import type { CurrentUser, LoginRequest, RegisterRequest } from "./types";

const AUTH = "/api/v1/auth";

/** The signed-in user, or `null` when nobody is. Any other failure is thrown. */
export async function fetchCurrentUser(): Promise<CurrentUser | null> {
  try {
    return await apiRequest<CurrentUser>(`${AUTH}/me`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      return null;
    }
    throw error;
  }
}

export function signIn(credentials: LoginRequest): Promise<CurrentUser> {
  return apiRequest<CurrentUser>(`${AUTH}/login`, { method: "POST", json: credentials });
}

export function registerAccount(details: RegisterRequest): Promise<CurrentUser> {
  return apiRequest<CurrentUser>(`${AUTH}/register`, { method: "POST", json: details });
}

export function signOut(): Promise<void> {
  return apiRequest<void>(`${AUTH}/logout`, { method: "POST" });
}

/**
 * Session hooks: who is signed in, and signing in, up and out.
 *
 * The API decides everything. These hooks ask it (`GET /auth/me`) and record its answer in the
 * query cache under SESSION_KEY; the route guards read that record. There is no auth state
 * anywhere else - nothing in localStorage, sessionStorage or a React context of our own.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";

import { fetchCurrentUser, registerAccount, signIn, signOut } from "../api/auth";
import type { CurrentUser, OrganisationContext } from "../api/types";
import { SESSION_KEY, endSession } from "./sessionCache";

/** A signed-in user who belongs to an organisation - what every page inside the app has. */
export type SignedInUser = CurrentUser & { organisation: OrganisationContext };

export function useSession() {
  return useQuery({
    queryKey: SESSION_KEY,
    queryFn: fetchCurrentUser,
    // Re-asked when the window regains focus after a minute, so a session that ended while the
    // tab was in the background is noticed when the person comes back.
    staleTime: 60_000,
  });
}

/**
 * The signed-in user inside the app. Only valid below `RequireAuth`, which does not render its
 * children until the session is known to have an organisation.
 */
export function useSignedInUser(): SignedInUser {
  const { data } = useSession();

  if (!data?.organisation) {
    throw new Error("useSignedInUser() is only valid inside <RequireAuth>.");
  }

  return data as SignedInUser;
}

export function useSignIn() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: signIn,
    // A 401 here means "wrong email or password", not "your session ended" (see queryClient.ts).
    meta: { credentialCheck: true },
    onSuccess: (user) => {
      queryClient.clear();
      queryClient.setQueryData(SESSION_KEY, user);
    },
  });
}

export function useRegister() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: registerAccount,
    meta: { credentialCheck: true },
    onSuccess: (user) => {
      queryClient.clear();
      queryClient.setQueryData(SESSION_KEY, user);
    },
  });
}

/** Sign out on the server, forget everything cached, and go to the sign-in page. */
export function useSignOut() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const mutation = useMutation({
    mutationFn: signOut,
    meta: { credentialCheck: true },
    onSuccess: () => {
      endSession(queryClient);
      navigate("/login", { replace: true });
    },
  });

  return {
    signOut: () => mutation.mutate(),
    isPending: mutation.isPending,
    error: mutation.error,
  };
}

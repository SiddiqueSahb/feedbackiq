import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError } from "../api/client";
import { SESSION_KEY, endSession } from "../auth/sessionCache";

declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: {
      /**
       * True for sign-in, registration and sign-out, where a 401 is an answer about the
       * credentials submitted rather than a sign that the current session has ended.
       */
      credentialCheck?: boolean;
    };
  }
}

function isUnauthorised(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

/**
 * How data from the API is cached, retried, and what a 401 means.
 *
 * **A 401 from any data request ends the session in the app.** The server has already refused:
 * the session expired, was signed out elsewhere, or the account was disabled. The app forgets
 * every cached response and marks itself signed out; the route guard then sends the person to
 * sign in again, remembering where they were.
 *
 * A client error (4xx) is an answer, not a glitch, so only network failures and server errors are
 * retried, twice.
 */
export function createQueryClient(): QueryClient {
  const queryClient: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        // The session query turns 401 into `null` itself; anything else signed out is a data query.
        if (isUnauthorised(error) && query.queryKey[0] !== SESSION_KEY[0]) {
          endSession(queryClient);
        }
      },
    }),
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (isUnauthorised(error) && !mutation.meta?.credentialCheck) {
          endSession(queryClient);
        }
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: (failureCount, error) => {
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
            return false;
          }
          return failureCount < 2;
        },
      },
      mutations: {
        retry: false,
      },
    },
  });

  return queryClient;
}

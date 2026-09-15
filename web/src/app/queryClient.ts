import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "../api/client";

/**
 * How data from the API is cached and retried.
 *
 * A client error (4xx) is an answer, not a glitch - retrying "not signed in" or "not found" only
 * delays showing it - so only network failures and server errors are retried, twice.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
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
}

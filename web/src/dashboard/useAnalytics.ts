import { useQuery } from "@tanstack/react-query";

import { fetchCategoryBreakdown, fetchSummary, fetchTrend, type TrendInterval } from "../api/analytics";
import type { AnalyticsSummary } from "../api/types";

/** Everything computed from analysed feedback. An upload invalidates the whole prefix. */
export const ANALYTICS_KEY = ["analytics"] as const;

/** How often the figures are re-read while feedback is still waiting for analysis. */
export const PENDING_ANALYSIS_POLL_MS = 10_000;

export function isWaitingForAnalysis(summary: AnalyticsSummary | undefined): boolean {
  return (summary?.not_analysed ?? 0) > 0;
}

export function useSummary() {
  return useQuery({
    queryKey: [...ANALYTICS_KEY, "summary"],
    queryFn: fetchSummary,
    // While feedback is waiting, the figures change as the worker finishes; once nothing is
    // waiting, polling stops.
    refetchInterval: (query) => (isWaitingForAnalysis(query.state.data) ? PENDING_ANALYSIS_POLL_MS : false),
  });
}

/**
 * The trend and the breakdown don't say how much is still waiting, so they follow the summary:
 * the page passes `poll` = "the summary says feedback is waiting".
 */
export function useTrend(interval: TrendInterval, poll: boolean) {
  return useQuery({
    queryKey: [...ANALYTICS_KEY, "trend", interval],
    queryFn: () => fetchTrend(interval),
    refetchInterval: poll ? PENDING_ANALYSIS_POLL_MS : false,
  });
}

export function useCategoryBreakdown(poll: boolean) {
  return useQuery({
    queryKey: [...ANALYTICS_KEY, "categories"],
    queryFn: fetchCategoryBreakdown,
    refetchInterval: poll ? PENDING_ANALYSIS_POLL_MS : false,
  });
}

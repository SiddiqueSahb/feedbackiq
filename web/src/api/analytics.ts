/**
 * Organisation analytics (Milestone 6 API): every figure is computed in PostgreSQL for the signed-in
 * user's organisation. Percentages are shares of *analysed* feedback, 0-100 with one decimal.
 */
import { apiRequest } from "./client";
import type { AnalyticsSummary, CategoryStat, TrendPoint } from "./types";

export type TrendInterval = "day" | "week" | "month";

export function fetchSummary(): Promise<AnalyticsSummary> {
  return apiRequest<AnalyticsSummary>("/api/v1/analytics/summary");
}

/** Feedback per period, oldest first. Periods with no feedback are absent, not zero. */
export function fetchTrend(interval: TrendInterval): Promise<TrendPoint[]> {
  return apiRequest<TrendPoint[]>(`/api/v1/analytics/trend?interval=${interval}`);
}

/**
 * Results per category, largest first. The entry with a null `category_key` holds results with
 * no category: positive feedback (not categorised by design) and complaints matching none.
 */
export function fetchCategoryBreakdown(): Promise<CategoryStat[]> {
  return apiRequest<CategoryStat[]>("/api/v1/analytics/categories");
}

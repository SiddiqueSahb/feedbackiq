/**
 * Organisation analytics (Milestone 6 API): every figure is computed in PostgreSQL for the signed-in
 * user's organisation. Percentages are shares of *analysed* feedback, 0-100 with one decimal.
 */
import { apiRequest } from "./client";
import type { AnalyticsSummary } from "./types";

export function fetchSummary(): Promise<AnalyticsSummary> {
  return apiRequest<AnalyticsSummary>("/api/v1/analytics/summary");
}

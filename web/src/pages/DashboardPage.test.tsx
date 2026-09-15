/**
 * The dashboard's headline figures - what each state of the organisation's data looks like.
 *
 * `fetch` is replaced by canned API answers; the hooks, cache and components are real.
 */
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnalyticsSummary } from "../api/types";
import { formatCompact, formatPercent } from "../dashboard/format";
import { isWaitingForAnalysis } from "../dashboard/useAnalytics";
import { fakeApi } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";

const OWNER = {
  email: "ana@acme.example",
  organisation: { id: "9b1c0000-0000-0000-0000-000000000001", name: "Acme Ltd", role: "owner" },
};

function figures(overrides: Partial<AnalyticsSummary> = {}): AnalyticsSummary {
  return {
    total_feedback: 12840,
    analysed: 12840,
    not_analysed: 0,
    analysis_pending: 0,
    analysis_failed: 0,
    sentiment_counts: { positive: 3210, neutral: 1284, negative: 8346 },
    sentiment_percentages: { positive: 25, neutral: 10, negative: 65 },
    unclassified: 834,
    unclassified_percentage: 6.5,
    average_rating: 2.43,
    earliest_feedback_at: "2026-01-02T08:00:00Z",
    latest_feedback_at: "2026-09-14T17:45:00Z",
    ...overrides,
  };
}

/** Three items, none analysed: combine with analysis_pending or analysis_failed. */
const NOTHING_ANALYSED_YET: Partial<AnalyticsSummary> = {
  total_feedback: 3, analysed: 0, not_analysed: 3, unclassified: 0, unclassified_percentage: 0,
  sentiment_counts: { positive: 0, neutral: 0, negative: 0 },
  sentiment_percentages: { positive: 0, neutral: 0, negative: 0 },
  average_rating: 1.3,
};

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const TREND = [
  { period: "2026-08-31T00:00:00+00:00", feedback_count: 80, negative: 30, neutral: 8, positive: 42, average_rating: 3.1 },
  { period: "2026-09-07T00:00:00+00:00", feedback_count: 85, negative: 55, neutral: 12, positive: 18, average_rating: 2.2 },
];

const CATEGORIES = [
  {
    category_key: "billing_and_payments", category: "Billing & Payments", count: 55, percentage: 33.3,
    sentiment_counts: { positive: 0, neutral: 5, negative: 50 }, average_confidence: 0.71,
  },
  {
    category_key: null, category: "Unclassified / Not categorised", count: 60, percentage: 36.4,
    sentiment_counts: { positive: 60, neutral: 0, negative: 0 }, average_confidence: null,
  },
];

async function openDashboard(
  summary: Parameters<typeof fakeApi>[0][string],
  extra: Parameters<typeof fakeApi>[0] = {},
) {
  const api = fakeApi({
    "GET /api/v1/auth/me": { status: 200, body: OWNER },
    "GET /api/v1/analytics/summary": summary,
    "GET /api/v1/analytics/trend?interval=week": { status: 200, body: TREND },
    "GET /api/v1/analytics/trend?interval=month": { status: 200, body: TREND },
    "GET /api/v1/analytics/categories": { status: 200, body: CATEGORIES },
    ...extra,
  });
  renderApp("/dashboard");
  await screen.findByRole("heading", { name: "Dashboard" });
  return api;
}

/** The tile whose label is `label`: its <dt> and the <dd> values beside it. */
function tile(label: string) {
  return screen.getByText(label, { selector: "dt" }).parentElement as HTMLElement;
}

describe("headline figures", () => {
  it("shows volume, sentiment shares, unclassified share and rating", async () => {
    await openDashboard({ status: 200, body: figures() });

    expect(await screen.findByText("Feedback", { selector: "dt" })).toBeInTheDocument();
    expect(within(tile("Feedback")).getByText("12.8k")).toBeInTheDocument();
    expect(within(tile("Feedback")).getByText("All analysed")).toBeInTheDocument();
    expect(within(tile("Negative")).getByText("65%")).toBeInTheDocument();
    expect(within(tile("Negative")).getByText("8,346 of 12.8k analysed")).toBeInTheDocument();
    expect(within(tile("Positive")).getByText("25%")).toBeInTheDocument();
    expect(within(tile("Unclassified")).getByText("6.5%")).toBeInTheDocument();
    expect(within(tile("Average rating")).getByText("2.4")).toBeInTheDocument();
    expect(screen.queryByText("Analysis in progress")).not.toBeInTheDocument();
  });

  it("says when the latest feedback arrived", async () => {
    await openDashboard({ status: 200, body: figures() });

    // "Sept" or "Sep" depending on the ICU data bundled with Node.
    const description = await screen.findByText(/latest feedback 14 Sept? 2026/);
    // A date, not a date and time: feedback dates are often date-only, and a time would be invented.
    expect(description.textContent).not.toMatch(/\d{1,2}:\d{2}/);
  });

  it("shows a dash, not a misleading rating, when no feedback carries one", async () => {
    await openDashboard({ status: 200, body: figures({ average_rating: null }) });

    expect(await screen.findByText("No ratings in this feedback")).toBeInTheDocument();
    expect(within(tile("Average rating")).getByText("—")).toBeInTheDocument();
  });
});

describe("states of the organisation's data", () => {
  it("invites an upload when there is no feedback yet", async () => {
    await openDashboard({
      status: 200,
      body: figures({
        total_feedback: 0, analysed: 0, not_analysed: 0, unclassified: 0, unclassified_percentage: 0,
        sentiment_counts: { positive: 0, neutral: 0, negative: 0 },
        sentiment_percentages: { positive: 0, neutral: 0, negative: 0 },
        average_rating: null, earliest_feedback_at: null, latest_feedback_at: null,
      }),
    });

    expect(await screen.findByRole("heading", { name: /fills in as feedback is analysed/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload feedback" })).toHaveAttribute("href", "/imports");
  });

  it("says analysis is in progress, and shows dashes rather than 0% before anything is analysed", async () => {
    await openDashboard({
      status: 200,
      body: figures({
        ...NOTHING_ANALYSED_YET,
        analysis_pending: 3,
      }),
    });

    expect(await screen.findByText("Analysis in progress")).toBeInTheDocument();
    expect(screen.getByText(/3 of 3 feedback items are still waiting/)).toBeInTheDocument();
    expect(within(tile("Negative")).getByText("—")).toBeInTheDocument();
    expect(within(tile("Negative")).getByText("Waiting for analysis")).toBeInTheDocument();
    expect(within(tile("Feedback")).getByText("0 analysed · 3 waiting")).toBeInTheDocument();
  });

  // The bug found in the Milestone 8 review: analysis that gave up read as "in progress" for ever.
  it("says analysis failed, not that it is in progress, when analysis gave up", async () => {
    await openDashboard({
      status: 200,
      body: figures({
        ...NOTHING_ANALYSED_YET,
        analysis_failed: 3,
      }),
    });

    expect(await screen.findByText("Analysis failed")).toBeInTheDocument();
    expect(screen.getByText(/3 of 3 feedback items could not be analysed/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View imports" })).toHaveAttribute("href", "/imports");
    expect(screen.queryByText("Analysis in progress")).not.toBeInTheDocument();
    expect(within(tile("Feedback")).getByText("0 analysed · 3 failed")).toBeInTheDocument();
    expect(within(tile("Negative")).getByText("Not analysed")).toBeInTheDocument();
  });

  it("tells waiting and failed feedback apart when both exist", async () => {
    await openDashboard({
      status: 200,
      body: figures({ total_feedback: 10, analysed: 5, not_analysed: 5, analysis_pending: 2, analysis_failed: 3 }),
    });

    expect(await screen.findByText("Analysis in progress")).toBeInTheDocument();
    expect(screen.getByText(/2 of 10 feedback items are still waiting/)).toBeInTheDocument();
    expect(screen.getByText("Analysis failed")).toBeInTheDocument();
    expect(screen.getByText(/3 of 10 feedback items could not be analysed/)).toBeInTheDocument();
    expect(within(tile("Feedback")).getByText("5 analysed · 2 waiting · 3 failed")).toBeInTheDocument();
  });

  it("offers a retry when the figures cannot be loaded", async () => {
    let healthy = false;
    await openDashboard(() =>
      healthy ? { status: 200, body: figures() } : { status: 503, body: { detail: "Service unavailable." } },
    );

    expect(await screen.findByText("The dashboard could not be loaded", {}, { timeout: 8000 })).toBeInTheDocument();

    healthy = true;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("65%")).toBeInTheDocument();
  }, 15000);
});

describe("trend and categories", () => {
  it("shows the sentiment trend and the category breakdown for the organisation", async () => {
    await openDashboard({ status: 200, body: figures() });

    expect(await screen.findByRole("img", { name: /Sentiment over time/ })).toBeInTheDocument();
    const categories = await screen.findByRole("list", { name: "Complaint categories" });
    expect(categories).toHaveTextContent("Billing & Payments");
    expect(screen.getByRole("list", { name: "Not categorised" })).toHaveTextContent("60");
  });

  it("groups the trend by week, and asks the API again when another grouping is chosen", async () => {
    const api = await openDashboard({ status: 200, body: figures() });
    await screen.findByRole("img", { name: /Sentiment over time/ });

    expect(screen.getByRole("button", { name: "Week" })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(screen.getByRole("button", { name: "Month" }));

    expect(screen.getByRole("button", { name: "Month" })).toHaveAttribute("aria-pressed", "true");
    await screen.findByText(/Analysed feedback per month/);
    expect(api.calls.some((call) => call.url === "/api/v1/analytics/trend?interval=month")).toBe(true);
  });

  it("keeps the rest of the dashboard when only the trend fails", async () => {
    await openDashboard(
      { status: 200, body: figures() },
      { "GET /api/v1/analytics/trend?interval=week": { status: 500, body: { detail: "Could not load the trend." } } },
    );

    expect(await screen.findByText("The trend could not be loaded", {}, { timeout: 8000 })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Complaint categories" })).toBeInTheDocument();
    expect(within(tile("Negative")).getByText("65%")).toBeInTheDocument();
  }, 15000);
});

describe("formatting and polling rules", () => {
  it("keeps exact digits below ten thousand and compacts above, in British style", () => {
    expect(formatCompact(9999)).toBe("9,999");
    expect(formatCompact(12840)).toBe("12.8k");
    expect(formatCompact(4_200_000)).toBe("4.2m");
  });

  it("shows whole percentages without a decimal", () => {
    expect(formatPercent(50)).toBe("50%");
    expect(formatPercent(66.7)).toBe("66.7%");
  });

  it("polls only while feedback is waiting for analysis", () => {
    expect(isWaitingForAnalysis(figures({ not_analysed: 2, analysis_pending: 2 }))).toBe(true);
    expect(isWaitingForAnalysis(figures({ not_analysed: 0 }))).toBe(false);
    expect(isWaitingForAnalysis(undefined)).toBe(false);
  });

  it("does not poll for analysis that failed, or for feedback no analysis is coming for", () => {
    expect(isWaitingForAnalysis(figures({ not_analysed: 2, analysis_failed: 2 }))).toBe(false);
    expect(isWaitingForAnalysis(figures({ not_analysed: 2 }))).toBe(false);
  });
});

describe("polling the figures", () => {
  /** How many times the page asks for the summary while `ms` pass, after its first load. */
  async function summaryRequestsDuring(ms: number, body: AnalyticsSummary) {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = await openDashboard({ status: 200, body });
    await screen.findByText("Feedback", { selector: "dt" });

    const count = () => api.calls.filter((call) => call.url === "/api/v1/analytics/summary").length;
    const before = count();
    await vi.advanceTimersByTimeAsync(ms);

    return count() - before;
  }

  it("re-reads the figures while analysis is pending", async () => {
    const requests = await summaryRequestsDuring(25_000, figures({ ...NOTHING_ANALYSED_YET, analysis_pending: 3 }));

    expect(requests).toBeGreaterThanOrEqual(2);
  });

  it("stops re-reading when the only unanalysed feedback is analysis that failed", async () => {
    const requests = await summaryRequestsDuring(25_000, figures({ ...NOTHING_ANALYSED_YET, analysis_failed: 3 }));

    expect(requests).toBe(0);
  });
});

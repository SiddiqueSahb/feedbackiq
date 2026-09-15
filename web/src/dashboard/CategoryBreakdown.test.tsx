import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CategoryStat } from "../api/types";
import { CategoryBreakdown } from "./CategoryBreakdown";

function stat(category_key: string | null, category: string, count: number, percentage: number): CategoryStat {
  return {
    category_key,
    category,
    count,
    percentage,
    sentiment_counts: { positive: 0, neutral: 0, negative: count },
    average_confidence: 0.7,
  };
}

const ROWS = [
  stat(null, "Unclassified / Not categorised", 120, 40),
  stat("wait_times_and_delays", "Wait Times & Delays", 90, 30),
  stat("billing_and_payments", "Billing & Payments", 60, 20),
  stat("app_and_technical_issues", "App & Technical Issues", 30, 10),
];

describe("CategoryBreakdown", () => {
  it("lists categories in the API's order with their count and share as text", () => {
    render(<CategoryBreakdown rows={ROWS} />);

    const items = within(screen.getByRole("list", { name: "Complaint categories" })).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      "Wait Times & Delays9030%",
      "Billing & Payments6020%",
      "App & Technical Issues3010%",
    ]);
  });

  it("draws bar lengths relative to the largest row", () => {
    const { container } = render(<CategoryBreakdown rows={ROWS} />);

    const widths = [...container.querySelectorAll<HTMLElement>("[class*='bar']")].map((bar) => bar.style.width);
    // Categorised rows first, then the uncategorised row (the largest, 120).
    expect(widths).toEqual(["75%", "50%", "25%", "100%"]);
  });

  it("sets the uncategorised results apart and explains what they are", () => {
    render(<CategoryBreakdown rows={ROWS} />);

    const apart = screen.getByRole("list", { name: "Not categorised" });
    expect(apart).toHaveTextContent("Not categorised12040%");
    expect(screen.getByText(/Positive feedback is not given a complaint category/)).toBeInTheDocument();
  });

  it("gives a small but non-zero count a visible sliver", () => {
    const { container } = render(
      <CategoryBreakdown rows={[stat("big", "Big", 10_000, 99.9), stat("tiny", "Tiny", 1, 0.1)]} />,
    );

    const bars = [...container.querySelectorAll<HTMLElement>("[class*='bar']")];
    expect(bars[1]!.style.width).toBe("0.75%");
  });

  it("says so when nothing is analysed", () => {
    render(<CategoryBreakdown rows={[]} />);

    expect(screen.getByText("No analysed feedback to break down yet.")).toBeInTheDocument();
  });
});

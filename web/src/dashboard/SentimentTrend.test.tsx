import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { TrendPoint } from "../api/types";
import { SentimentTrend } from "./SentimentTrend";

function point(period: string, negative: number, neutral: number, positive: number): TrendPoint {
  return { period, feedback_count: negative + neutral + positive, negative, neutral, positive, average_rating: null };
}

const WEEKS = [
  point("2026-08-17T00:00:00+00:00", 40, 10, 20),
  point("2026-08-24T00:00:00+00:00", 55, 12, 18),
  point("2026-08-31T00:00:00+00:00", 30, 8, 42),
];

describe("SentimentTrend", () => {
  it("names every series in a legend, so colour is never the only key", () => {
    render(<SentimentTrend points={WEEKS} interval="week" />);

    const legend = screen.getByRole("list", { name: "Legend" });
    expect(within(legend).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Negative",
      "Neutral",
      "Positive",
    ]);
  });

  it("draws one column per period, with one segment per non-zero sentiment", () => {
    const { container } = render(
      <SentimentTrend points={[...WEEKS, point("2026-09-07T00:00:00+00:00", 5, 0, 0)]} interval="week" />,
    );

    const columns = container.querySelectorAll("[data-period]");
    expect(columns).toHaveLength(4);
    // The last week has only negative feedback: one segment, which takes the rounded top.
    expect(columns[3]!.querySelectorAll("rect:not([class*='hit'])")).toHaveLength(0);
    expect(columns[3]!.querySelectorAll("path")).toHaveLength(1);
  });

  it("reads each period with the arrow keys once the chart has focus", async () => {
    render(<SentimentTrend points={WEEKS} interval="week" />);

    await userEvent.tab();
    expect(screen.getByRole("img", { name: /Sentiment over time/ })).toHaveFocus();

    // Focus starts on the latest period.
    const readout = screen.getByRole("status");
    expect(readout).toHaveTextContent(/w\/c 31 Aug/);
    expect(readout).toHaveTextContent("42 positive");
    expect(readout).toHaveTextContent("80 analysed");

    await userEvent.keyboard("{ArrowLeft}");
    expect(screen.getByRole("status")).toHaveTextContent("55 negative");

    await userEvent.keyboard("{Home}");
    expect(screen.getByRole("status")).toHaveTextContent(/w\/c 17 Aug/);
  });

  it("places the readout beside the column, on whichever side has room, never over it", async () => {
    render(<SentimentTrend points={WEEKS} interval="week" />);

    await userEvent.tab();
    // Focus starts on the latest period, at the right-hand end: the readout sits to its left.
    expect(screen.getByRole("status")).toHaveAttribute("data-side", "left");

    await userEvent.keyboard("{Home}");
    // The first period, at the left-hand end: the readout sits to its right.
    expect(screen.getByRole("status")).toHaveAttribute("data-side", "right");
  });

  it("shows the same readout on hover", () => {
    const { container } = render(<SentimentTrend points={WEEKS} interval="week" />);

    const hitAreas = container.querySelectorAll("rect[class*='hit']");
    fireEvent.pointerEnter(hitAreas[1]!);

    expect(screen.getByRole("status")).toHaveTextContent("55 negative");
  });

  it("offers every value in a table, so nothing is reachable only by hovering", async () => {
    render(<SentimentTrend points={WEEKS} interval="week" />);

    await userEvent.click(screen.getByRole("button", { name: "Show table" }));

    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(4); // header + three weeks
    expect(rows[2]).toHaveTextContent(/w\/c 24 Aug\s*55\s*12\s*18\s*85/);
    expect(screen.getByRole("button", { name: "Hide table" })).toHaveAttribute("aria-expanded", "true");
  });

  it("says so instead of drawing an empty chart when nothing is analysed", () => {
    render(<SentimentTrend points={[point("2026-08-31T00:00:00+00:00", 0, 0, 0)]} interval="week" />);

    expect(screen.getByText("No analysed feedback in this period yet.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});

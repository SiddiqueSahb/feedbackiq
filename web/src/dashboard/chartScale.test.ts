import { describe, expect, it } from "vitest";

import { formatPeriod, labelIndexes, niceScale, roundedTopColumn } from "./chartScale";

describe("niceScale", () => {
  it.each([
    [0, 1, [0, 1]],
    [3, 3, [0, 1, 2, 3]],
    [87, 100, [0, 25, 50, 75, 100]],
    [100, 100, [0, 25, 50, 75, 100]],
    [1234, 1500, [0, 500, 1000, 1500]],
  ])("a maximum of %d gets an axis to %d", (value, max, ticks) => {
    expect(niceScale(value)).toEqual({ max, ticks });
  });

  it("never puts a fraction on an axis of counts, however small the largest count", () => {
    // Feedback is counted in whole items: "0.5 feedback" on an axis is nonsense. 9 is the case a
    // plain minimum step would miss: it picks a step of 2.5.
    for (const value of [1, 2, 5, 9]) {
      expect(niceScale(value).ticks.every(Number.isInteger), `ticks for ${value}`).toBe(true);
    }
  });

  it("always reaches at least the largest value", () => {
    for (const value of [1, 7, 19, 250, 999, 4321, 98765]) {
      expect(niceScale(value).max).toBeGreaterThanOrEqual(value);
    }
  });
});

describe("labelIndexes", () => {
  it("labels every period when they fit", () => {
    expect(labelIndexes(5)).toEqual([0, 1, 2, 3, 4]);
  });

  it("thins labels on a long axis but always labels the latest period", () => {
    const indexes = labelIndexes(52);

    expect(indexes.length).toBeLessThanOrEqual(7);
    expect(indexes.at(-1)).toBe(51);
  });
});

describe("formatPeriod", () => {
  it("formats in UTC, so a period starting at midnight UTC keeps its date everywhere", () => {
    expect(formatPeriod("2026-09-01T00:00:00+00:00", "day")).toMatch(/^1 Sept?$/);
    expect(formatPeriod("2026-09-07T00:00:00+00:00", "week")).toMatch(/^w\/c 7 Sept?$/);
    expect(formatPeriod("2026-09-01T00:00:00+00:00", "month")).toMatch(/^Sept? 2026$/);
  });
});

describe("roundedTopColumn", () => {
  it("never rounds more than the column's height or half its width", () => {
    expect(roundedTopColumn(0, 10, 4, 1)).toContain("Q 0 10 1 10");
  });
});

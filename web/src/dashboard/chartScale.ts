/**
 * The arithmetic behind the dashboard's charts. Pure functions, so every rule is testable without
 * rendering anything.
 */
import type { TrendInterval } from "../api/analytics";

/**
 * A y-axis of counts that ends on a round number, with evenly spaced whole-number ticks from zero.
 *
 *   niceScale(0)    → { max: 1,    ticks: [0, 1] }
 *   niceScale(3)    → { max: 3,    ticks: [0, 1, 2, 3] }
 *   niceScale(87)   → { max: 100,  ticks: [0, 25, 50, 75, 100] }
 *   niceScale(1234) → { max: 1500, ticks: [0, 500, 1000, 1500] }
 *
 * Steps are always whole numbers: feedback is counted in items, and "0.5" or "7.5" on a count axis
 * is nonsense. So a candidate step that is not an integer (0.25, 0.5, 2.5 at small scales) is
 * skipped, and no step is smaller than 1.
 */
export function niceScale(value: number, targetTicks = 4): { max: number; ticks: number[] } {
  if (!Number.isFinite(value) || value <= 0) {
    return { max: 1, ticks: [0, 1] };
  }

  const rough = value / targetTicks;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const candidate = [1, 2, 2.5, 5, 10]
    .map((multiple) => multiple * magnitude)
    .find((step) => step >= rough && Number.isInteger(step));
  const step = Math.max(1, candidate ?? Math.ceil(rough));
  const max = Math.ceil(value / step) * step;

  const ticks: number[] = [];
  for (let tick = 0; tick <= max + step / 2; tick += step) {
    ticks.push(Math.round(tick * 1000) / 1000);
  }

  return { max, ticks };
}

/**
 * Which x-axis positions get a label: all of them when they fit, otherwise about `most` evenly
 * spaced ones, always including the latest period.
 */
export function labelIndexes(count: number, most = 7): number[] {
  if (count <= most) {
    return Array.from({ length: count }, (_, index) => index);
  }

  const step = Math.ceil((count - 1) / (most - 1));
  const indexes: number[] = [];
  for (let index = count - 1; index >= 0; index -= step) {
    indexes.unshift(index);
  }

  return indexes;
}

// Periods are the start of a day, week or month in UTC (date_trunc in PostgreSQL), so they are
// formatted in UTC: in a local time zone behind UTC, "1 Sept 00:00 UTC" would read as 31 August.
const dayFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const monthFormat = new Intl.DateTimeFormat("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });

export function formatPeriod(period: string, interval: TrendInterval): string {
  const date = new Date(period);

  if (Number.isNaN(date.getTime())) return period;
  if (interval === "month") return monthFormat.format(date);
  if (interval === "week") return `w/c ${dayFormat.format(date)}`;
  return dayFormat.format(date);
}

/** Rounded top corners only: the data end is rounded, the baseline stays square. */
export function roundedTopColumn(x: number, y: number, width: number, height: number, radius = 4): string {
  const r = Math.max(0, Math.min(radius, width / 2, height));

  return [
    `M ${x} ${y + height}`,
    `V ${y + r}`,
    `Q ${x} ${y} ${x + r} ${y}`,
    `H ${x + width - r}`,
    `Q ${x + width} ${y} ${x + width} ${y + r}`,
    `V ${y + height}`,
    "Z",
  ].join(" ");
}

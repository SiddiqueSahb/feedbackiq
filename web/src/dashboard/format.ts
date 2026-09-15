const whole = new Intl.NumberFormat("en-GB");
const compact = new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 1 });

/**
 * 1,284 up to 9,999; beyond that 12.8k, 4.2m and 1.5bn, where exact digits stop helping a
 * headline. Lowercase k/m/bn is the en-GB convention the rest of the app follows.
 */
export function formatCompact(value: number): string {
  return Math.abs(value) < 10_000 ? whole.format(value) : compact.format(value);
}

/** The API sends 0-100 with one decimal: 66.7 → "66.7%", 50 → "50%". */
export function formatPercent(value: number): string {
  return `${Number.isInteger(value) ? value : value.toFixed(1)}%`;
}

export function formatRating(value: number): string {
  return value.toFixed(1);
}

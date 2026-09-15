import { useState, type KeyboardEvent } from "react";

import type { TrendInterval } from "../api/analytics";
import type { TrendPoint } from "../api/types";
import { formatPeriod, labelIndexes, niceScale, roundedTopColumn } from "./chartScale";
import { formatCompact } from "./format";
import styles from "./SentimentTrend.module.css";

// The chart is drawn in a fixed coordinate space and scaled to the card's width by the SVG viewBox.
const WIDTH = 720;
const PLOT_TOP = 8;
const PLOT_HEIGHT = 200;
const AXIS_BAND = 28;
const PLOT_LEFT = 48;
const PLOT_RIGHT = 8;
const HEIGHT = PLOT_TOP + PLOT_HEIGHT + AXIS_BAND;
const PLOT_WIDTH = WIDTH - PLOT_LEFT - PLOT_RIGHT;

const MAX_COLUMN_WIDTH = 24;
/** The surface-coloured gap that separates stacked segments. */
const SEGMENT_GAP = 2;

/**
 * Bottom to top. Neutral sits between the poles so negative and positive never touch - the order
 * the sentiment colours were validated for (styles/tokens.css).
 */
const SERIES = [
  { key: "negative", label: "Negative" },
  { key: "neutral", label: "Neutral" },
  { key: "positive", label: "Positive" },
] as const;

type SeriesKey = (typeof SERIES)[number]["key"];

function analysed(point: TrendPoint): number {
  return point.negative + point.neutral + point.positive;
}

interface SentimentTrendProps {
  points: TrendPoint[];
  interval: TrendInterval;
}

/**
 * Analysed feedback per period, stacked by sentiment.
 *
 * Every value is reachable three ways: the tooltip (pointer or arrow keys once the chart has focus),
 * and the table view - the tooltip only ever adds to the other two. Only analysed feedback is
 * plotted; feedback still waiting is shown by the page's "Analysis in progress" notice.
 */
export function SentimentTrend({ points, interval }: SentimentTrendProps) {
  const [active, setActive] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);

  if (points.length === 0 || points.every((point) => analysed(point) === 0)) {
    return <p className={styles.empty}>No analysed feedback in this period yet.</p>;
  }

  const { max, ticks } = niceScale(Math.max(...points.map(analysed)));
  const band = PLOT_WIDTH / points.length;
  const columnWidth = Math.max(2, Math.min(MAX_COLUMN_WIDTH, band * 0.7));
  const yFor = (value: number) => PLOT_TOP + PLOT_HEIGHT - (value / max) * PLOT_HEIGHT;
  const labelled = new Set(labelIndexes(points.length));
  const activePoint = active === null ? null : points[active];

  // Beside the hovered column, never on top of it: to its right in the left half of the chart and
  // to its left in the right half, so it never hides the values it describes or runs off the edge.
  const tooltipOnRight = active !== null && PLOT_LEFT + (active + 0.5) * band < WIDTH / 2;
  const tooltipStyle =
    active === null
      ? undefined
      : tooltipOnRight
        ? { left: `calc(${((PLOT_LEFT + (active + 1) * band) / WIDTH) * 100}% + 8px)` }
        : { right: `calc(${(1 - (PLOT_LEFT + active * band) / WIDTH) * 100}% + 8px)` };

  function handleKey(event: KeyboardEvent<SVGSVGElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight" && event.key !== "Home" && event.key !== "End") {
      return;
    }
    event.preventDefault();
    const last = points.length - 1;
    const current = active ?? last;
    const next =
      event.key === "Home" ? 0
        : event.key === "End" ? last
          : event.key === "ArrowLeft" ? Math.max(0, current - 1)
            : Math.min(last, current + 1);
    setActive(next);
  }

  return (
    <figure className={styles.figure}>
      <ul className={styles.legend} aria-label="Legend">
        {SERIES.map((series) => (
          <li key={series.key} className={styles.legendItem}>
            <span className={`${styles.swatch} ${styles[series.key]}`} aria-hidden="true" />
            {series.label}
          </li>
        ))}
      </ul>

      {/* On a narrow screen the chart keeps a readable minimum width and scrolls sideways inside
          this container, rather than shrinking its labels to a few pixels. */}
      <div className={styles.scroll}>
      <div className={styles.plot}>
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          className={styles.svg}
          role="img"
          tabIndex={0}
          aria-label="Sentiment over time. Use the left and right arrow keys to read each period, or show the table."
          onKeyDown={handleKey}
          onFocus={() => setActive((current) => current ?? points.length - 1)}
          onBlur={() => setActive(null)}
          onPointerLeave={() => setActive(null)}
        >
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                x1={PLOT_LEFT}
                x2={WIDTH - PLOT_RIGHT}
                y1={yFor(tick)}
                y2={yFor(tick)}
                className={tick === 0 ? styles.baseline : styles.grid}
              />
              <text x={PLOT_LEFT - 8} y={yFor(tick)} className={styles.tick} textAnchor="end" dominantBaseline="middle">
                {formatCompact(tick)}
              </text>
            </g>
          ))}

          {points.map((point, index) => {
            const x = PLOT_LEFT + index * band + (band - columnWidth) / 2;
            const drawn = SERIES.filter((series) => point[series.key] > 0);
            let running = 0;

            return (
              <g key={point.period} data-period={point.period}>
                {index === active && (
                  <rect x={PLOT_LEFT + index * band} y={PLOT_TOP} width={band} height={PLOT_HEIGHT} className={styles.activeBand} />
                )}

                {drawn.map((series, position) => {
                  const value = point[series.key as SeriesKey];
                  const bottom = yFor(running);
                  running += value;
                  const top = yFor(running);
                  const isTop = position === drawn.length - 1;
                  // Every segment but the top one gives up its last 2px to the surface gap.
                  const height = Math.max(0, bottom - top - (isTop ? 0 : SEGMENT_GAP));

                  return isTop ? (
                    <path key={series.key} d={roundedTopColumn(x, top, columnWidth, height)} className={styles[series.key]} />
                  ) : (
                    <rect key={series.key} x={x} y={top + SEGMENT_GAP} width={columnWidth} height={height} className={styles[series.key]} />
                  );
                })}

                {labelled.has(index) && (
                  <text x={x + columnWidth / 2} y={HEIGHT - 8} className={styles.tick} textAnchor="middle">
                    {formatPeriod(point.period, interval)}
                  </text>
                )}

                {/* The hit area is the whole band, far bigger than a thin column. */}
                <rect
                  x={PLOT_LEFT + index * band}
                  y={PLOT_TOP}
                  width={band}
                  height={PLOT_HEIGHT}
                  className={styles.hit}
                  onPointerEnter={() => setActive(index)}
                />
              </g>
            );
          })}
        </svg>

        {activePoint && active !== null && (
          <div
            className={styles.tooltip}
            style={tooltipStyle}
            data-side={tooltipOnRight ? "right" : "left"}
            role="status"
            aria-live="polite"
          >
            <p className={styles.tooltipTitle}>{formatPeriod(activePoint.period, interval)}</p>
            {[...SERIES].reverse().map((series) => (
              <p key={series.key} className={styles.tooltipRow}>
                <span className={`${styles.key} ${styles[series.key]}`} aria-hidden="true" />
                <strong>{formatCompact(activePoint[series.key])}</strong> {series.label.toLowerCase()}
              </p>
            ))}
            <p className={styles.tooltipFoot}>{formatCompact(analysed(activePoint))} analysed</p>
          </div>
        )}
      </div>
      </div>

      <figcaption className={styles.caption}>
        <span>Analysed feedback per {interval}, by sentiment.</span>
        <button type="button" className={styles.toggle} aria-expanded={showTable} onClick={() => setShowTable((shown) => !shown)}>
          {showTable ? "Hide table" : "Show table"}
        </button>
      </figcaption>

      {showTable && (
        <div className={styles.tableScroll}>
          <table className={styles.table}>
            <caption className="visually-hidden">Analysed feedback per {interval}, by sentiment</caption>
            <thead>
              <tr>
                <th scope="col">Period</th>
                {SERIES.map((series) => (
                  <th key={series.key} scope="col" className={styles.numeric}>
                    {series.label}
                  </th>
                ))}
                <th scope="col" className={styles.numeric}>
                  Analysed
                </th>
              </tr>
            </thead>
            <tbody>
              {points.map((point) => (
                <tr key={point.period}>
                  <th scope="row">{formatPeriod(point.period, interval)}</th>
                  {SERIES.map((series) => (
                    <td key={series.key} className={styles.numeric}>
                      {formatCompact(point[series.key])}
                    </td>
                  ))}
                  <td className={styles.numeric}>{formatCompact(analysed(point))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </figure>
  );
}

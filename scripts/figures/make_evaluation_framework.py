"""
Draw Figure 3.3 — the evaluation framework across the three pipeline stages.

    python scripts/figures/make_evaluation_framework.py

Writes docs/figures/arch_evaluation_framework.{png,pdf} (paths Chapter 3
already references).

Row labels sit in a left gutter rather than a bottom strip -- the previous
layout read as a 4th tier and dropped "Metrics". Layout is driven by the
constants below, not hand-placed coordinates.
"""

from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT_DIR = os.path.join("docs", "figures")
BASENAME = "arch_evaluation_framework"

# geometry
GUTTER = 1.35          # width of the row-label column
COL_W = 2.85           # box width
COL_GAP = 0.42
COL_X = [GUTTER + 0.30 + i * (COL_W + COL_GAP) for i in range(3)]

# Rows are stacked bottom-up from ROW_OUTPUT with explicit gaps, so the
# spacing stays even if any box height changes. The two larger gaps are where
# a horizontal bus has to run (input fans out to three stages; three baselines
# collect back into one output).
H_INPUT = 0.72
H_STAGE = 0.86
H_METRIC = 1.72
H_BASE = 0.86
H_OUTPUT = 0.80

GAP = 0.60          # normal gap between rows
GAP_BUS = 0.90      # gap where a horizontal connector runs

ROW_OUTPUT = 1.20
ROW_BASE = ROW_OUTPUT + H_OUTPUT + GAP_BUS
ROW_METRIC = ROW_BASE + H_BASE + GAP
ROW_STAGE = ROW_METRIC + H_METRIC + GAP
ROW_INPUT = ROW_STAGE + H_STAGE + GAP_BUS

Y_TOP = ROW_INPUT + H_INPUT + 1.30   # headroom for title + subtitle
Y_BOTTOM = ROW_OUTPUT - 0.35

FULL_X = COL_X[0]
FULL_W = COL_X[-1] + COL_W - COL_X[0]

# palette
GREY_F, GREY_E = "#ECEDEF", "#9AA0A6"
BLUE_F, BLUE_E = "#DCE9F7", "#2E6DB4"
WHITE_F, WHITE_E = "#FFFFFF", "#B9BDC2"
ORANGE_F, ORANGE_E = "#FBE3D0", "#D2721E"
GREEN_F, GREEN_E = "#DFF0DC", "#4B8B3B"
ARROW = "#4A4A4A"
INK = "#1F1F1F"

STAGES = [
    ("Sentiment", "labels available"),
    ("Taxonomy", "no ground truth"),
    ("Generation", "reference-free"),
]

METRICS = [
    ["accuracy · balanced accuracy",
     "macro and weighted F1",
     "per-class precision / recall / F1",
     "confusion matrices",
     "McNemar's exact test"],
    ["centroid separation",
     "within-category coherence",
     "category balance",
     "platform spread",
     "unclassified rate"],
    ["faithfulness",
     "answer relevancy",
     "context precision",
     "context recall",
     "latency"],
]

BASELINES = [
    ("VADER", "lexicon floor"),
    ("Manual inspection", "human check"),
    ("Standalone LLM", "no retrieval"),
]

ROW_LABELS = [
    ("Input", ROW_INPUT, H_INPUT),
    ("Stage", ROW_STAGE, H_STAGE),
    ("Metrics", ROW_METRIC, H_METRIC),
    ("Baseline", ROW_BASE, H_BASE),
    ("Output", ROW_OUTPUT, H_OUTPUT),
]


def box(ax, x, y, w, h, face, edge, lw=1.4):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0,rounding_size=0.09",
        linewidth=lw, facecolor=face, edgecolor=edge, zorder=2,
    ))


def arrow(ax, x0, y0, x1, y1):
    ax.add_patch(FancyArrowPatch(
        (x0, y0), (x1, y1),
        arrowstyle="-|>", mutation_scale=13,
        linewidth=1.3, color=ARROW,
        shrinkA=0, shrinkB=0, zorder=3,
    ))


def main() -> int:
    fig, ax = plt.subplots(figsize=(10.4, 9.0))
    ax.set_xlim(0, GUTTER + 0.30 + 3 * COL_W + 2 * COL_GAP + 0.30)
    ax.set_ylim(Y_BOTTOM, Y_TOP)
    ax.axis("off")

    # titles
    mid = FULL_X + FULL_W / 2
    ax.text(mid, Y_TOP - 0.32, "Evaluation Framework",
            ha="center", va="center", fontsize=19, fontweight="bold", color=INK)
    ax.text(mid, Y_TOP - 0.82,
            "Metrics matched to what each stage permits, with a baseline at every stage",
            ha="center", va="center", fontsize=10.5, color="#555555")

    # row labels in the gutter
    for name, y, h in ROW_LABELS:
        ax.text(GUTTER, y + h / 2, name,
                ha="right", va="center", fontsize=11.5,
                fontweight="bold", color="#6B7075")

    # input
    box(ax, FULL_X, ROW_INPUT, FULL_W, H_INPUT, GREY_F, GREY_E)
    ax.text(mid, ROW_INPUT + H_INPUT / 2, "Pipeline Outputs",
            ha="center", va="center", fontsize=13, fontweight="bold", color=INK)

    # bus from the input box down to the three stage boxes
    bus_y = ROW_STAGE + H_STAGE + GAP_BUS / 2
    arrow_stub = 0.001
    ax.plot([mid, mid], [ROW_INPUT, bus_y], color=ARROW, lw=1.3, zorder=1)
    ax.plot([COL_X[0] + COL_W / 2, COL_X[-1] + COL_W / 2], [bus_y, bus_y],
            color=ARROW, lw=1.3, zorder=1)

    for i, (title, sub) in enumerate(STAGES):
        cx = COL_X[i] + COL_W / 2
        arrow(ax, cx, bus_y, cx, ROW_STAGE + H_STAGE + arrow_stub)

        box(ax, COL_X[i], ROW_STAGE, COL_W, H_STAGE, BLUE_F, BLUE_E, lw=1.6)
        ax.text(cx, ROW_STAGE + H_STAGE * 0.62, title,
                ha="center", va="center", fontsize=13, fontweight="bold", color=INK)
        ax.text(cx, ROW_STAGE + H_STAGE * 0.26, sub,
                ha="center", va="center", fontsize=9.5, color="#5A5F63")

        # stage -> metrics
        arrow(ax, cx, ROW_STAGE, cx, ROW_METRIC + H_METRIC + arrow_stub)

        # metrics panel
        box(ax, COL_X[i], ROW_METRIC, COL_W, H_METRIC, WHITE_F, WHITE_E, lw=1.1)
        lines = METRICS[i]
        step = H_METRIC / (len(lines) + 1)
        for j, line in enumerate(lines, start=1):
            ax.text(cx, ROW_METRIC + H_METRIC - j * step, line,
                    ha="center", va="center", fontsize=9.3, color="#3C4043")

        # metrics -> baseline
        arrow(ax, cx, ROW_METRIC, cx, ROW_BASE + H_BASE + arrow_stub)

        # baseline
        b_title, b_sub = BASELINES[i]
        box(ax, COL_X[i], ROW_BASE, COL_W, H_BASE, ORANGE_F, ORANGE_E, lw=1.6)
        ax.text(cx, ROW_BASE + H_BASE * 0.62, b_title,
                ha="center", va="center", fontsize=12, fontweight="bold", color=INK)
        ax.text(cx, ROW_BASE + H_BASE * 0.26, b_sub,
                ha="center", va="center", fontsize=9.5, color="#5A5F63")

        # baseline -> collector bus
        ax.plot([cx, cx], [ROW_BASE, ROW_OUTPUT + H_OUTPUT + GAP_BUS / 2],
                color=ARROW, lw=1.3, zorder=1)

    # collector bus into the output box
    collect_y = ROW_OUTPUT + H_OUTPUT + GAP_BUS / 2
    ax.plot([COL_X[0] + COL_W / 2, COL_X[-1] + COL_W / 2], [collect_y, collect_y],
            color=ARROW, lw=1.3, zorder=1)
    arrow(ax, mid, collect_y, mid, ROW_OUTPUT + H_OUTPUT + arrow_stub)

    box(ax, FULL_X, ROW_OUTPUT, FULL_W, H_OUTPUT, GREEN_F, GREEN_E, lw=1.6)
    ax.text(mid, ROW_OUTPUT + H_OUTPUT * 0.63, "data/results/",
            ha="center", va="center", fontsize=13, fontweight="bold",
            color=INK, family="monospace")
    ax.text(mid, ROW_OUTPUT + H_OUTPUT * 0.25,
            "every reported figure written to file by the script that produced it",
            ha="center", va="center", fontsize=9.5, color="#4A5A45")

    os.makedirs(OUT_DIR, exist_ok=True)
    for ext, dpi in (("png", 260), ("pdf", 260)):
        path = os.path.join(OUT_DIR, f"{BASENAME}.{ext}")
        fig.savefig(path, dpi=dpi, bbox_inches="tight",
                    pad_inches=0.16, facecolor="white")
        print(f"wrote {path}")
    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

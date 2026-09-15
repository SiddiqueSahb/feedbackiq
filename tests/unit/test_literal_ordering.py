"""
Literal types spelled in one order - src/feedbackiq

`typing` caches `Literal[...] | None` by value, and treats two Literals with the same values in a
different order as equal. So `Literal["positive", "negative", "neutral"] | None` and
`Literal["positive", "neutral", "negative"] | None` become *one* cached object, carrying whichever
order was created first - which depends on import order.

The visible symptom (Milestone 8): FastAPI's OpenAPI document listed a sentiment enum in a
different order depending on which modules a process had imported first, so the committed
`web/openapi.json` and the generated frontend types churned between runs.

This test reads the source rather than importing it, so its result cannot depend on import order.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src" / "feedbackiq"


def literal_value_orders() -> dict[frozenset, set[tuple]]:
    """Every `Literal[...]` of plain constants, grouped by its set of values."""
    orders: dict[frozenset, set[tuple]] = defaultdict(set)

    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript):
                continue

            name = node.value.attr if isinstance(node.value, ast.Attribute) else getattr(node.value, "id", None)
            if name != "Literal":
                continue

            elements = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            if len(elements) < 2 or not all(isinstance(e, ast.Constant) for e in elements):
                continue

            values = tuple(e.value for e in elements)
            orders[frozenset(values)].add(values)

    return orders


def test_literals_with_the_same_values_are_written_in_the_same_order():
    conflicts = {
        tuple(sorted(map(str, values))): sorted(spellings)
        for values, spellings in literal_value_orders().items()
        if len(spellings) > 1
    }

    assert conflicts == {}, (
        "These Literal types list the same values in different orders, so which order reaches "
        f"the OpenAPI document depends on import order. Use one order, or one shared alias: {conflicts}"
    )


def test_the_scan_actually_finds_literals():
    """Guards against the scan silently matching nothing and passing vacuously."""
    assert frozenset({"positive", "neutral", "negative"}) in literal_value_orders()

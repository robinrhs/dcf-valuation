"""Sensitivity of value per share to WACC and terminal growth.

The two inputs that move a DCF the most are the discount rate and the perpetual growth
rate (they sit in the denominator of the Gordon formula, WACC - g). A grid shows how
fragile the point estimate is: small changes often swing the value by 20-40%.
"""

from __future__ import annotations

from collections.abc import Callable


def centered_range(center: float, step: float, steps_each_side: int) -> list[float]:
    """E.g. center 9%, step 0.5%, 2 steps -> [8.0%, 8.5%, 9.0%, 9.5%, 10.0%]."""
    return [center + step * i for i in range(-steps_each_side, steps_each_side + 1)]


def sensitivity_grid(
    value_fn: Callable[[float, float], float],
    waccs: list[float],
    growths: list[float],
) -> list[list[float | None]]:
    """Rows = WACC, columns = terminal growth. ``None`` where WACC <= g (formula undefined)."""
    grid: list[list[float | None]] = []
    for wacc in waccs:
        row: list[float | None] = []
        for growth in growths:
            row.append(value_fn(wacc, growth) if wacc > growth else None)
        grid.append(row)
    return grid

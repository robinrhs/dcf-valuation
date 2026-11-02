"""Reverse DCF: which revenue growth does today's share price already "price in"?

Instead of asking "what is the company worth given my growth assumption?", we ask the
reverse: holding WACC, margins, reinvestment and terminal growth fixed, find the
*constant* annual revenue growth g over the explicit horizon such that

    DCF value per share (g) = current share price.

This is a root-finding problem f(g) = value(g) - price = 0, solved with Brent's method
(scipy.optimize.brentq), which needs an interval [low, high] where f changes sign.
"""

from __future__ import annotations

from collections.abc import Callable

from scipy.optimize import brentq

DEFAULT_LOW = -0.50  # -50% p.a.
DEFAULT_HIGH = 1.00  # +100% p.a.


def implied_growth(
    value_for_growth: Callable[[float], float],
    share_price: float,
    low: float = DEFAULT_LOW,
    high: float = DEFAULT_HIGH,
) -> float | None:
    """Constant revenue growth at which the model value equals the share price.

    Returns None if no solution exists inside [low, high] (e.g. even -50% growth p.a.
    gives a value above the price, or the business never earns its cost of capital).
    """

    def gap(growth: float) -> float:
        return value_for_growth(growth) - share_price

    gap_low, gap_high = gap(low), gap(high)
    if gap_low == 0:
        return low
    if gap_high == 0:
        return high
    if (gap_low > 0) == (gap_high > 0):
        return None  # no sign change -> no root in the interval
    return float(brentq(gap, low, high, xtol=1e-10))

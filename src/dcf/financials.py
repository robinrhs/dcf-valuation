"""Historical ratios that anchor the projection (growth, margins, reinvestment).

A DCF projection should start from what the company has actually delivered. The
ratios here are the "base rates" that scenario assumptions default to when the YAML
leaves a value empty (``null``).
"""

from __future__ import annotations

from dataclasses import dataclass

from dcf.xbrl import FiscalYearData


@dataclass(frozen=True)
class HistoricalSummary:
    """Key ratios from the reported fiscal years (all as decimals, 0.05 = 5%)."""

    last_fiscal_year: int
    last_revenue: float
    last_nwc: float
    revenue_cagr: float | None
    last_ebit_margin: float | None
    avg_da_pct_revenue: float | None
    avg_capex_pct_revenue: float | None
    avg_nwc_pct_revenue: float | None
    avg_effective_tax_rate: float | None
    years_used: int


def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    """numerator / denominator, or None if either is missing or the denominator is 0."""
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def average(values: list[float | None]) -> float | None:
    """Mean of the available values; None if nothing is available."""
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def cagr(first: float, last: float, periods: int) -> float | None:
    """Compound annual growth rate: (last / first)^(1 / periods) - 1."""
    if periods <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / periods) - 1


def revenue_growth_rates(years: list[FiscalYearData]) -> list[float | None]:
    """Year-over-year revenue growth; the first year has no prior year (None)."""
    rates: list[float | None] = [None]
    for prev, cur in zip(years, years[1:], strict=False):
        rates.append(safe_ratio(cur.revenue - prev.revenue, prev.revenue))
    return rates


def summarize(years: list[FiscalYearData], lookback: int = 3) -> HistoricalSummary:
    """Summarise the last ``lookback`` fiscal years (oldest first in ``years``).

    Averages over several years smooth one-off effects; the CAGR uses the same window.
    The margin uses the latest year because it is the jumping-off point of the forecast.
    """
    if not years:
        raise ValueError("No fiscal years available to summarise")
    window = years[-lookback:]
    last = window[-1]
    return HistoricalSummary(
        last_fiscal_year=last.fiscal_year,
        last_revenue=last.revenue,
        last_nwc=last.net_working_capital,
        revenue_cagr=cagr(window[0].revenue, last.revenue, len(window) - 1),
        last_ebit_margin=safe_ratio(last.ebit, last.revenue),
        avg_da_pct_revenue=average(
            [safe_ratio(y.depreciation_amortization, y.revenue) for y in window]
        ),
        avg_capex_pct_revenue=average([safe_ratio(y.capex, y.revenue) for y in window]),
        avg_nwc_pct_revenue=average([safe_ratio(y.net_working_capital, y.revenue) for y in window]),
        avg_effective_tax_rate=average(
            [safe_ratio(y.income_tax_expense, y.pretax_income) for y in window]
        ),
        years_used=len(window),
    )

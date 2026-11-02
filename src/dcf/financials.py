"""Historical ratios that anchor the projection (growth, margins, reinvestment).

A DCF projection should start from what the company has actually delivered. The
ratios here are the "base rates" that scenario assumptions default to when the YAML
leaves a value empty (``null``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from dcf.xbrl import FiscalYearData

# D&A sources that come from the notes (owned property and intangibles). The cash flow
# statement totals (DepreciationDepletionAndAmortization etc.) normally already contain
# the amortisation of finance-lease ROU assets; these note-level figures usually do not.
NOTE_LEVEL_DA_SOURCES = {"Depreciation + AmortizationOfIntangibleAssets", "Depreciation"}


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


def include_finance_leases(year: FiscalYearData) -> FiscalYearData:
    """Treat finance leases like assets bought with borrowed money.

    A finance lease adds an asset and a liability without any cash capex. If the liability
    is subtracted in the bridge as debt, the asset must also count as investment, or the
    leased data centres come in for free:
        capex  += ROU assets obtained in exchange for new finance-lease liabilities
        D&A    += finance-lease ROU amortisation (non-cash, inside EBIT), but only when D&A
                  was rebuilt from note-level depreciation that does not already include it
    Calling it twice changes nothing.
    """
    if year.includes_finance_leases:
        return year
    sources = dict(year.sources)
    capex = year.capex
    if year.finance_lease_additions is not None:
        capex = (capex or 0.0) + year.finance_lease_additions
        sources["capex"] = (
            sources.get("capex", "n/a")
            + " + RightOfUseAssetObtainedInExchangeForFinanceLeaseLiability"
        )
    da = year.depreciation_amortization
    da_source = sources.get("depreciation_amortization")
    if year.finance_lease_amortization is not None and da_source in NOTE_LEVEL_DA_SOURCES:
        da = (da or 0.0) + year.finance_lease_amortization
        sources["depreciation_amortization"] = (
            f"{da_source} + FinanceLeaseRightOfUseAssetAmortization"
        )
    return replace(
        year,
        capex=capex,
        depreciation_amortization=da,
        sources=sources,
        includes_finance_leases=True,
    )


def historical_ufcf(years: list[FiscalYearData], tax_rate: float) -> list[float | None]:
    """Reported-basis unlevered FCF: EBIT * (1 - t) + D&A - capex - dNWC.

    Same definition as the projection, so history and forecast can be compared directly.
    The first year has no prior NWC (None), as does any year with missing EBIT.
    """
    result: list[float | None] = [None]
    for prev, cur in zip(years, years[1:], strict=False):
        if cur.ebit is None:
            result.append(None)
            continue
        delta_nwc = cur.net_working_capital - prev.net_working_capital
        result.append(
            cur.ebit * (1 - tax_rate)
            + (cur.depreciation_amortization or 0.0)
            - (cur.capex or 0.0)
            - delta_nwc
        )
    return result


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

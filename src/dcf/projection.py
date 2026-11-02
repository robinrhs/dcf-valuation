"""Scenario assumptions -> projected unlevered free cash flow (UFCF).

Unlevered FCF, step by step (all per projection year):

    Revenue_t   = Revenue_{t-1} * (1 + g_t)
    EBIT_t      = Revenue_t * EBIT margin_t
    NOPAT_t     = EBIT_t * (1 - tax rate)          taxes as if the firm had no debt
    + D&A_t     = Revenue_t * D&A %                non-cash expense, added back
    - Capex_t   = Revenue_t * capex %              cash spent on fixed assets
    - dNWC_t    = NWC_t - NWC_{t-1}, NWC_t = Revenue_t * NWC %
    = UFCF_t

"Unlevered" means before interest: the cash flow belongs to debt and equity holders
together, which is why it is discounted at the WACC.

Paths over the forecast horizon:
- Revenue growth fades linearly from the year-1 rate to the terminal growth rate, so the
  final explicit year already grows at the perpetual rate (a steady state).
- Margins, D&A % and capex % move linearly from the starting level (default: history) to a
  target level, reaching the target in the final year.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from dcf.financials import HistoricalSummary


@dataclass(frozen=True)
class ScenarioSpec:
    """Scenario as written in YAML. ``None`` means "derive from history"."""

    name: str
    revenue_growth_start: float | None = None  # year-1 growth; None -> historical CAGR
    revenue_growth_shift: float = 0.0  # added to year-1 growth (e.g. bull = +0.03)
    revenue_growth_path: tuple[float, ...] | None = None  # explicit growth per year
    ebit_margin_start: float | None = None  # None -> last reported EBIT margin
    ebit_margin_target: float | None = None  # None -> keep the starting margin
    ebit_margin_shift: float = 0.0  # added to the target margin
    da_pct_revenue: float | None = None  # None -> historical average
    da_pct_revenue_target: float | None = None
    capex_pct_revenue: float | None = None  # None -> historical average
    capex_pct_revenue_target: float | None = None
    nwc_pct_revenue: float | None = None  # None -> historical average
    terminal_growth: float = 0.025
    exit_multiple: float = 12.0  # EV / EBITDA applied to the final explicit year


@dataclass(frozen=True)
class Scenario:
    """Fully resolved scenario: one value per projection year, no missing inputs."""

    name: str
    revenue_growth: tuple[float, ...]
    ebit_margin: tuple[float, ...]
    da_pct_revenue: tuple[float, ...]
    capex_pct_revenue: tuple[float, ...]
    nwc_pct_revenue: float
    tax_rate: float
    terminal_growth: float
    exit_multiple: float

    @property
    def years(self) -> int:
        return len(self.revenue_growth)


@dataclass(frozen=True)
class ProjectionYear:
    t: int  # 1 = first forecast year
    fiscal_year: int
    revenue: float
    revenue_growth: float
    ebit_margin: float
    ebit: float
    taxes: float
    nopat: float
    depreciation_amortization: float
    capex: float
    nwc: float
    delta_nwc: float
    ufcf: float

    @property
    def ebitda(self) -> float:
        return self.ebit + self.depreciation_amortization


def fade_path(start: float, end: float, years: int) -> tuple[float, ...]:
    """Linear fade where year 1 = start and the final year = end (growth rates)."""
    if years == 1:
        return (end,)
    step = (end - start) / (years - 1)
    return tuple(start + step * i for i in range(years))


def glide_path(start: float, target: float, years: int) -> tuple[float, ...]:
    """Linear move from today's level towards a target, reached in the final year.

    Year t value = start + (target - start) * t / years, so year 1 already moves a bit and
    there is no jump from the last reported year (margins, capex %, D&A %).
    """
    return tuple(start + (target - start) * t / years for t in range(1, years + 1))


def _pick(value: float | None, fallback: float | None, label: str) -> float:
    chosen = value if value is not None else fallback
    if chosen is None:
        raise ValueError(f"No value for {label!r}: set it in the scenario YAML")
    return chosen


def resolve_scenario(
    spec: ScenarioSpec, history: HistoricalSummary, years: int, tax_rate: float
) -> Scenario:
    """Fill gaps in a YAML scenario with historical ratios and build per-year paths."""
    if not 5 <= years <= 10:
        raise ValueError("Projection horizon must be between 5 and 10 years")

    if spec.revenue_growth_path is not None:
        if len(spec.revenue_growth_path) != years:
            raise ValueError(f"revenue_growth_path needs exactly {years} values")
        growth = tuple(g + spec.revenue_growth_shift for g in spec.revenue_growth_path)
    else:
        g1 = _pick(spec.revenue_growth_start, history.revenue_cagr, "revenue_growth_start")
        growth = fade_path(g1 + spec.revenue_growth_shift, spec.terminal_growth, years)

    margin_start = _pick(spec.ebit_margin_start, history.last_ebit_margin, "ebit_margin_start")
    margin_target = (
        spec.ebit_margin_target if spec.ebit_margin_target is not None else margin_start
    ) + spec.ebit_margin_shift

    da_start = _pick(spec.da_pct_revenue, history.avg_da_pct_revenue, "da_pct_revenue")
    capex_start = _pick(spec.capex_pct_revenue, history.avg_capex_pct_revenue, "capex_pct_revenue")
    nwc_pct = _pick(spec.nwc_pct_revenue, history.avg_nwc_pct_revenue, "nwc_pct_revenue")

    return Scenario(
        name=spec.name,
        revenue_growth=growth,
        ebit_margin=glide_path(margin_start, margin_target, years),
        da_pct_revenue=glide_path(da_start, _pick(spec.da_pct_revenue_target, da_start, ""), years),
        capex_pct_revenue=glide_path(
            capex_start, _pick(spec.capex_pct_revenue_target, capex_start, ""), years
        ),
        nwc_pct_revenue=nwc_pct,
        tax_rate=tax_rate,
        terminal_growth=spec.terminal_growth,
        exit_multiple=spec.exit_multiple,
    )


def with_constant_growth(scenario: Scenario, growth: float) -> Scenario:
    """Same scenario, but revenue grows at one constant rate in every explicit year."""
    return replace(scenario, revenue_growth=(growth,) * scenario.years)


def project(
    scenario: Scenario, base_revenue: float, base_nwc: float, base_fiscal_year: int
) -> list[ProjectionYear]:
    """Build the explicit forecast, one row per year.

    ``base_nwc`` is the actual NWC of the last reported year, so year-1 dNWC reflects the
    move from today's balance sheet to the projected NWC level.
    """
    rows: list[ProjectionYear] = []
    revenue, nwc_prev = base_revenue, base_nwc
    for i in range(scenario.years):
        growth = scenario.revenue_growth[i]
        revenue = revenue * (1 + growth)
        ebit = revenue * scenario.ebit_margin[i]
        taxes = ebit * scenario.tax_rate  # operating taxes on EBIT (no interest tax shield)
        nopat = ebit - taxes
        da = revenue * scenario.da_pct_revenue[i]
        capex = revenue * scenario.capex_pct_revenue[i]
        nwc = revenue * scenario.nwc_pct_revenue
        delta_nwc = nwc - nwc_prev  # an increase in NWC ties up cash -> reduces FCF
        rows.append(
            ProjectionYear(
                t=i + 1,
                fiscal_year=base_fiscal_year + i + 1,
                revenue=revenue,
                revenue_growth=growth,
                ebit_margin=scenario.ebit_margin[i],
                ebit=ebit,
                taxes=taxes,
                nopat=nopat,
                depreciation_amortization=da,
                capex=capex,
                nwc=nwc,
                delta_nwc=delta_nwc,
                ufcf=nopat + da - capex - delta_nwc,
            )
        )
        nwc_prev = nwc
    return rows

"""Wire the pieces together: history -> WACC -> projection -> TV -> bridge -> checks.

No downloads happen here: the caller passes in company facts and a market quote, so the
whole valuation can be reproduced offline from a fixture.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from dcf.assumptions import Assumptions
from dcf.financials import (
    HistoricalSummary,
    diluted_share_count,
    include_finance_leases,
    summarize,
)
from dcf.market import MarketQuote
from dcf.projection import ProjectionYear, Scenario, project, resolve_scenario, with_constant_growth
from dcf.reverse_dcf import implied_growth
from dcf.sensitivity import centered_range, sensitivity_grid
from dcf.valuation import (
    BridgeInputs,
    ValuationResult,
    implied_exit_multiple,
    implied_perpetual_growth,
    implied_terminal_roic,
    value_exit_multiple,
    value_gordon,
)
from dcf.wacc import WaccInputs, WaccResult, compute_wacc
from dcf.xbrl import (
    FiscalYearData,
    extract_fiscal_years,
    latest_shares_outstanding,
    missing_items,
)

# If the cover-page share count differs from the fiscal-year weighted average by more than
# this, something is off (e.g. it covers only one share class): fall back to the average.
MAX_SHARE_COUNT_GAP = 0.25

# Below this gap between WACC and terminal growth the Gordon TV explodes: 1 / (WACC - g)
# doubles when the gap halves, so small input changes swing the value per share.
MIN_WACC_GROWTH_SPREAD = 0.02


@dataclass(frozen=True)
class CompanyData:
    ticker: str
    name: str
    cik: int | None
    fiscal_years: list[FiscalYearData]
    shares_outstanding: float | None = None  # latest cover-page share count
    shares_outstanding_date: str | None = None

    @property
    def latest(self) -> FiscalYearData:
        return self.fiscal_years[-1]


@dataclass(frozen=True)
class CrossChecks:
    implied_exit_multiple: float | None  # EV/EBITDA implied by the Gordon TV
    implied_perpetual_growth: float | None  # growth implied by the exit-multiple TV
    implied_terminal_roic: float | None


@dataclass(frozen=True)
class SensitivityTable:
    waccs: list[float]
    growths: list[float]
    values: list[list[float | None]]  # value per share, rows = WACC, cols = growth


@dataclass(frozen=True)
class DcfResult:
    company: CompanyData
    history: HistoricalSummary
    scenario: Scenario
    quote: MarketQuote
    wacc_inputs: WaccInputs
    wacc: WaccResult
    bridge: BridgeInputs
    mid_year: bool
    projection: list[ProjectionYear]
    gordon: ValuationResult
    exit_multiple: ValuationResult
    checks: CrossChecks
    sensitivity: SensitivityTable | None
    implied_growth: float | None
    warnings: list[str] = field(default_factory=list)


def company_from_facts(ticker: str, facts: dict, max_years: int = 5) -> CompanyData:
    years = extract_fiscal_years(facts, max_years=max_years)
    if not years:
        raise ValueError(f"No annual 10-K revenue found for {ticker}")
    cover = latest_shares_outstanding(facts)
    return CompanyData(
        ticker=ticker.upper(),
        name=facts.get("entityName", ticker),
        cik=facts.get("cik"),
        fiscal_years=years,
        shares_outstanding=cover[0] if cover else None,
        shares_outstanding_date=cover[1] if cover else None,
    )


def select_share_count(company: CompanyData, override: float | None) -> tuple[float, str]:
    """Share count for value per share, and a description of where it came from.

    Preferred: latest cover-page shares outstanding + last year's dilution (diluted minus
    basic weighted average). The fiscal-year weighted average alone lags buybacks and
    issuance, so it is only the fallback.
    """
    if override is not None:
        if override <= 0:
            raise ValueError(f"market.diluted_shares must be positive, got {override}")
        return override, "manual override (market.diluted_shares)"
    latest = company.latest
    diluted, basic = latest.diluted_shares, latest.basic_shares
    cover, cover_date = company.shares_outstanding, company.shares_outstanding_date
    if cover and cover_date and cover_date >= latest.period_end and diluted and basic:
        shares = diluted_share_count(cover, diluted, basic)
        if abs(shares / diluted - 1) <= MAX_SHARE_COUNT_GAP:
            return shares, (
                f"shares outstanding {cover_date} (cover page) + FY{latest.fiscal_year} "
                "dilution (diluted - basic weighted average)"
            )
    if not diluted:
        raise ValueError("Diluted shares missing in filings: set market.diluted_shares in YAML")
    return diluted, f"FY{latest.fiscal_year} weighted-average diluted shares (10-K)"


def prepare_company(company: CompanyData, assumptions: Assumptions) -> CompanyData:
    """Apply the lease treatment chosen in YAML to all historical years."""
    if not assumptions.balance_sheet.finance_leases_as_debt:
        return company
    return replace(company, fiscal_years=[include_finance_leases(y) for y in company.fiscal_years])


def build_bridge(company: CompanyData, assumptions: Assumptions, price: float) -> BridgeInputs:
    """Debt, leases, cash and share count from the latest 10-K, unless overridden in YAML."""
    latest = company.latest
    sheet = assumptions.balance_sheet
    shares, shares_source = select_share_count(company, assumptions.market.diluted_shares)
    if sheet.finance_lease_liabilities is not None:
        leases = sheet.finance_lease_liabilities
    elif sheet.finance_leases_as_debt:
        leases = latest.finance_lease_liabilities or 0.0
    else:
        leases = 0.0
    return BridgeInputs(
        debt=latest.total_debt if sheet.debt is None else sheet.debt,
        cash=latest.cash_and_investments if sheet.cash is None else sheet.cash,
        diluted_shares=shares,
        share_price=price,
        lease_liabilities=leases,
        shares_source=shares_source,
    )


def build_wacc(
    assumptions: Assumptions, quote: MarketQuote, bridge: BridgeInputs
) -> tuple[WaccInputs, WaccResult]:
    """CAPM-based WACC with market-value equity and book-value debt weights.

    D includes finance-lease liabilities: whatever is subtracted as debt in the bridge must
    also be part of the capital whose cost the WACC measures.
    """
    inputs = WaccInputs(
        risk_free_rate=assumptions.wacc.risk_free_rate,
        beta=quote.beta,
        equity_risk_premium=assumptions.wacc.equity_risk_premium,
        pretax_cost_of_debt=assumptions.wacc.cost_of_debt,
        tax_rate=assumptions.tax_rate,
        market_cap=quote.price * bridge.diluted_shares,
        debt=bridge.debt_like,
    )
    result = compute_wacc(inputs)
    if assumptions.wacc.wacc_override is not None:
        result = WaccResult(
            cost_of_equity=result.cost_of_equity,
            after_tax_cost_of_debt=result.after_tax_cost_of_debt,
            equity_weight=result.equity_weight,
            debt_weight=result.debt_weight,
            wacc=assumptions.wacc.wacc_override,
        )
    return inputs, result


def gordon_value_per_share(
    scenario: Scenario,
    history: HistoricalSummary,
    wacc: float,
    terminal_growth: float,
    bridge: BridgeInputs,
    mid_year: bool,
) -> float:
    """Value per share for one set of inputs (used by sensitivity and reverse DCF)."""
    rows = project(scenario, history.last_revenue, history.last_nwc, history.last_fiscal_year)
    return value_gordon(rows, wacc, terminal_growth, bridge, mid_year).value_per_share


def terminal_warnings(
    final_ufcf: float, final_ebitda: float, checks: CrossChecks, wacc: float
) -> list[str]:
    """Flags for a terminal year that the TV formulas cannot handle sensibly."""
    messages: list[str] = []
    if final_ufcf <= 0:
        messages.append(
            "Final-year FCF is not positive: the Gordon terminal value, the implied growth "
            "cross-check and the TV share of EV are meaningless"
        )
    if final_ebitda <= 0:
        messages.append("Final-year EBITDA is not positive: the exit-multiple TV is meaningless")
    roic = checks.implied_terminal_roic
    if roic is not None and roic < wacc:
        # g = reinvestment rate x ROIC. In a value-driver model, where reinvestment rises
        # with g (TV = NOPAT_{N+1} (1 - g / RONIC) / (WACC - g)), growth at a return below
        # the WACC destroys value. The Gordon TV used here holds UFCF_N - and therefore the
        # reinvestment - fixed while g changes, so its value still rises with g.
        messages.append(
            f"Implied return on new capital ({roic:.1%}) is below the WACC ({wacc:.2%}): "
            "the final year reinvests a lot for little growth. In a value-driver model "
            "(TV = NOPAT_N+1 x (1 - g/RONIC) / (WACC - g)) more growth at this return would "
            "destroy value; this Gordon TV holds UFCF_N fixed, so it overstates what a "
            "higher g is worth (the sensitivity grid still rises with g)"
        )
    return messages


def growth_warnings(terminal_growth: float, wacc: float, risk_free_rate: float) -> list[str]:
    """Flags for a terminal growth rate that is implausibly high or too close to the WACC."""
    messages: list[str] = []
    if terminal_growth > risk_free_rate:
        # The long-term risk-free rate is a common ceiling for perpetual nominal growth:
        # no firm can outgrow the whole economy forever.
        messages.append(
            f"Terminal growth ({terminal_growth:.2%}) is above the risk-free rate "
            f"({risk_free_rate:.2%}), a common ceiling for perpetual nominal growth"
        )
    spread = wacc - terminal_growth
    if spread < MIN_WACC_GROWTH_SPREAD:
        messages.append(
            f"WACC - g is only {spread * 100:.2f}pp: the Gordon TV multiplies UFCF_N by "
            f"{(1 + terminal_growth) / spread:.0f}x and reacts very strongly to small changes "
            "in WACC or g"
        )
    return messages


def run_dcf(
    company: CompanyData,
    quote: MarketQuote,
    assumptions: Assumptions,
    scenario_name: str = "base",
    with_sensitivity: bool = True,
) -> DcfResult:
    if scenario_name not in assumptions.scenarios:
        raise ValueError(
            f"Unknown scenario {scenario_name!r}; available: {sorted(assumptions.scenarios)}"
        )
    company = prepare_company(company, assumptions)
    settings = assumptions.projection
    mid_year = settings.mid_year_convention
    history = summarize(company.fiscal_years, lookback=settings.history_lookback)
    scenario = resolve_scenario(
        assumptions.scenarios[scenario_name], history, settings.years, assumptions.tax_rate
    )
    bridge = build_bridge(company, assumptions, quote.price)
    wacc_inputs, wacc = build_wacc(assumptions, quote, bridge)

    rows = project(scenario, history.last_revenue, history.last_nwc, history.last_fiscal_year)
    gordon = value_gordon(rows, wacc.wacc, scenario.terminal_growth, bridge, mid_year)
    exit_result = value_exit_multiple(rows, wacc.wacc, scenario.exit_multiple, bridge, mid_year)

    final = rows[-1]
    checks = CrossChecks(
        implied_exit_multiple=implied_exit_multiple(
            gordon.terminal_value, final.ebitda, wacc.wacc, mid_year
        ),
        implied_perpetual_growth=implied_perpetual_growth(
            exit_result.terminal_value, final.ufcf, wacc.wacc, mid_year
        ),
        implied_terminal_roic=implied_terminal_roic(final, scenario.terminal_growth),
    )

    sensitivity = None
    if with_sensitivity:
        s = assumptions.sensitivity
        waccs = centered_range(wacc.wacc, s.wacc_step, s.steps_each_side)
        growths = centered_range(scenario.terminal_growth, s.growth_step, s.steps_each_side)
        grid = sensitivity_grid(
            lambda w, g: gordon_value_per_share(scenario, history, w, g, bridge, mid_year),
            waccs,
            growths,
        )
        sensitivity = SensitivityTable(waccs, growths, grid)

    # Reverse DCF: constant revenue growth that makes the Gordon value equal the price.
    reverse = implied_growth(
        lambda g: gordon_value_per_share(
            with_constant_growth(scenario, g),
            history,
            wacc.wacc,
            scenario.terminal_growth,
            bridge,
            mid_year,
        ),
        quote.price,
    )

    warnings = [
        f"FY{company.latest.fiscal_year}: no XBRL value for {item} (treated as 0 where used)"
        for item in missing_items(company.latest)
    ]
    if history.years_used < settings.history_lookback:
        warnings.append(
            f"Only {history.years_used} fiscal years available: historical averages use "
            f"{history.years_used}, not history_lookback = {settings.history_lookback}"
        )
    warnings.extend(
        growth_warnings(scenario.terminal_growth, wacc.wacc, assumptions.wacc.risk_free_rate)
    )
    warnings.extend(terminal_warnings(final.ufcf, final.ebitda, checks, wacc.wacc))

    return DcfResult(
        company=company,
        history=history,
        scenario=scenario,
        quote=quote,
        wacc_inputs=wacc_inputs,
        wacc=wacc,
        bridge=bridge,
        mid_year=mid_year,
        projection=rows,
        gordon=gordon,
        exit_multiple=exit_result,
        checks=checks,
        sensitivity=sensitivity,
        implied_growth=reverse,
        warnings=warnings,
    )

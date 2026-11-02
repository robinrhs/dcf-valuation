"""Wire the pieces together: history -> WACC -> projection -> TV -> bridge -> checks.

No downloads happen here: the caller passes in company facts and a market quote, so the
whole valuation can be reproduced offline from a fixture.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dcf.assumptions import Assumptions
from dcf.financials import HistoricalSummary, summarize
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
from dcf.xbrl import FiscalYearData, extract_fiscal_years, missing_items


@dataclass(frozen=True)
class CompanyData:
    ticker: str
    name: str
    cik: int | None
    fiscal_years: list[FiscalYearData]

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
    return CompanyData(ticker.upper(), facts.get("entityName", ticker), facts.get("cik"), years)


def build_bridge(company: CompanyData, assumptions: Assumptions, price: float) -> BridgeInputs:
    """Debt, cash and share count from the latest 10-K, unless overridden in YAML."""
    latest = company.latest
    shares = assumptions.market.diluted_shares or latest.diluted_shares
    if not shares:
        raise ValueError("Diluted shares missing in filings: set market.diluted_shares in YAML")
    debt = assumptions.balance_sheet.debt
    cash = assumptions.balance_sheet.cash
    return BridgeInputs(
        debt=latest.total_debt if debt is None else debt,
        cash=latest.cash_and_investments if cash is None else cash,
        diluted_shares=shares,
        share_price=price,
    )


def build_wacc(
    assumptions: Assumptions, quote: MarketQuote, bridge: BridgeInputs
) -> tuple[WaccInputs, WaccResult]:
    """CAPM-based WACC with market-value equity and book-value debt weights."""
    inputs = WaccInputs(
        risk_free_rate=assumptions.wacc.risk_free_rate,
        beta=quote.beta,
        equity_risk_premium=assumptions.wacc.equity_risk_premium,
        pretax_cost_of_debt=assumptions.wacc.cost_of_debt,
        tax_rate=assumptions.tax_rate,
        market_cap=quote.price * bridge.diluted_shares,
        debt=bridge.debt,
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
        # g = reinvestment rate x ROIC. If new capital earns less than it costs, every
        # extra point of terminal growth destroys value instead of creating it.
        messages.append(
            f"Implied return on new capital ({roic:.1%}) is below the WACC ({wacc:.2%}): "
            "terminal growth destroys value in this scenario, so a higher g lowers the value"
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

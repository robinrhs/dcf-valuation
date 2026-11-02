"""Plain-text report and CSV/JSON export of a DCF result (presentation only, no maths)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from dcf.financials import historical_ufcf, revenue_growth_rates, safe_ratio
from dcf.model import DcfResult
from dcf.valuation import discount_factor
from dcf.xbrl import FiscalYearData

DISCLAIMER = "Educational model, not investment advice. Output is only as good as the inputs."
MILLION = 1_000_000


def money(value: float | None) -> str:
    """USD millions with thousands separators, e.g. 331,839."""
    return "n/a" if value is None else f"{value / MILLION:,.0f}"


def pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def table(header: list[str], rows: list[list[str]], first_width: int = 26) -> str:
    """Right-aligned columns after a left-aligned label column."""
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    widths[0] = max(widths[0], first_width)
    lines = []
    for row in [header, *rows]:
        cells = [row[0].ljust(widths[0])] + [
            c.rjust(w) for c, w in zip(row[1:], widths[1:], strict=True)
        ]
        lines.append("  ".join(cells))
    lines.insert(1, "-" * len(lines[0]))
    return "\n".join(lines)


def historicals_table(years: list[FiscalYearData], tax_rate: float) -> str:
    growth = revenue_growth_rates(years)
    ufcf = historical_ufcf(years, tax_rate)
    header = ["USD m (10-K)"] + [f"FY{y.fiscal_year}" for y in years]
    rows = [
        ["Revenue"] + [money(y.revenue) for y in years],
        ["  growth"] + [pct(g) for g in growth],
        ["EBIT (operating income)"] + [money(y.ebit) for y in years],
        ["  margin"] + [pct(safe_ratio(y.ebit, y.revenue)) for y in years],
        ["D&A"] + [money(y.depreciation_amortization) for y in years],
        ["Capex"] + [money(y.capex) for y in years],
        ["Operating NWC"] + [money(y.net_working_capital) for y in years],
        [f"UFCF (t = {pct(tax_rate, 0)})"] + [money(u) for u in ufcf],
        ["Total debt"] + [money(y.total_debt) for y in years],
        ["Cash + ST investments"] + [money(y.cash_and_investments) for y in years],
        ["Diluted shares (m)"] + [money(y.diluted_shares) for y in years],
    ]
    return table(header, rows)


def sources_lines(year: FiscalYearData) -> str:
    return "\n".join(f"  {name:<26} {tag}" for name, tag in sorted(year.sources.items()))


def render(result: DcfResult) -> str:
    r = result
    c = r.company
    latest = c.latest
    w, wi = r.wacc, r.wacc_inputs
    g, x = r.gordon, r.exit_multiple
    out: list[str] = []

    out.append(f"DCF valuation: {c.name} ({c.ticker})  |  scenario: {r.scenario.name}")
    out.append(
        f"Financials: SEC EDGAR companyfacts, annual 10-K, latest FY{latest.fiscal_year} "
        f"(period end {latest.period_end})"
    )
    out.append(f"Market data: {r.quote.source}, as of {r.quote.as_of}")
    out.append(
        f"Convention: {'mid-year' if r.mid_year else 'end-of-year'} discounting, "
        f"{r.scenario.years}-year explicit forecast"
    )
    out.append(DISCLAIMER)

    out.append("\nHISTORICALS")
    out.append(historicals_table(c.fiscal_years, r.scenario.tax_rate))

    out.append("\nWACC")
    wacc_rows = [
        ["Risk-free rate", pct(wi.risk_free_rate, 2)],
        ["Beta", f"{wi.beta:.2f}"],
        ["Equity risk premium", pct(wi.equity_risk_premium, 2)],
        ["Cost of equity (CAPM)", pct(w.cost_of_equity, 2)],
        ["Pre-tax cost of debt", pct(wi.pretax_cost_of_debt, 2)],
        ["After-tax cost of debt", pct(w.after_tax_cost_of_debt, 2)],
        ["Equity weight E/V", pct(w.equity_weight)],
        ["Debt weight D/V", pct(w.debt_weight)],
        ["WACC", pct(w.wacc, 2)],
    ]
    out.append(table(["Input", "Value"], wacc_rows))

    out.append("\nPROJECTION")
    p = r.projection
    proj_rows = [
        ["Revenue growth"] + [pct(row.revenue_growth) for row in p],
        ["Revenue"] + [money(row.revenue) for row in p],
        ["EBIT margin"] + [pct(row.ebit_margin) for row in p],
        ["EBIT"] + [money(row.ebit) for row in p],
        ["- Taxes on EBIT"] + [money(row.taxes) for row in p],
        ["= NOPAT"] + [money(row.nopat) for row in p],
        ["+ D&A"] + [money(row.depreciation_amortization) for row in p],
        ["- Capex"] + [money(row.capex) for row in p],
        ["- Increase in NWC"] + [money(row.delta_nwc) for row in p],
        ["= Unlevered FCF"] + [money(row.ufcf) for row in p],
        ["Discount factor"] + [f"{discount_factor(w.wacc, row.t, r.mid_year):.3f}" for row in p],
        ["PV of UFCF"]
        + [money(row.ufcf * discount_factor(w.wacc, row.t, r.mid_year)) for row in p],
    ]
    out.append(table(["USD m"] + [f"FY{row.fiscal_year}E" for row in p], proj_rows))

    out.append("\nTERMINAL VALUE AND EQUITY BRIDGE")
    sc = r.scenario
    bridge_rows = [
        ["Assumption", f"g = {pct(sc.terminal_growth)}", f"{sc.exit_multiple:.1f}x EBITDA"],
        ["Terminal value (year N)", money(g.terminal_value), money(x.terminal_value)],
        ["PV of terminal value", money(g.pv_terminal), money(x.pv_terminal)],
        ["PV of explicit FCF", money(g.pv_explicit), money(x.pv_explicit)],
        ["Enterprise value", money(g.enterprise_value), money(x.enterprise_value)],
        ["- Debt", money(r.bridge.debt), money(r.bridge.debt)],
        ["+ Cash & ST investments", money(r.bridge.cash), money(r.bridge.cash)],
        ["= Equity value", money(g.equity_value), money(x.equity_value)],
        ["Diluted shares (m)", money(r.bridge.diluted_shares), money(r.bridge.diluted_shares)],
        ["Value per share (USD)", f"{g.value_per_share:,.2f}", f"{x.value_per_share:,.2f}"],
        ["Current price (USD)", f"{r.quote.price:,.2f}", f"{r.quote.price:,.2f}"],
        ["Upside / downside", pct(g.upside), pct(x.upside)],
        ["TV share of EV", pct(g.terminal_share_of_ev), pct(x.terminal_share_of_ev)],
    ]
    out.append(table(["USD m", "Gordon growth", "Exit multiple"], bridge_rows))

    out.append("\nCROSS-CHECKS")
    ck = r.checks
    out.append(
        f"  Gordon TV implies an exit multiple of      {ck.implied_exit_multiple:.1f}x EBITDA"
    )
    out.append(f"  Exit-multiple TV implies perpetual growth  {pct(ck.implied_perpetual_growth)}")
    roic = ck.implied_terminal_roic
    out.append(
        "  Implied ROIC on new capital (final year)   "
        + (pct(roic) if roic is not None else "n/a (no net reinvestment)")
    )

    if r.sensitivity is not None:
        s = r.sensitivity
        out.append(
            "\nSENSITIVITY: value per share (Gordon), rows = WACC, columns = terminal growth"
        )
        rows = [
            [pct(wacc)] + [f"{v:,.0f}" if v is not None else "n/a" for v in values]
            for wacc, values in zip(s.waccs, s.values, strict=True)
        ]
        out.append(table(["WACC \\ g"] + [pct(gr) for gr in s.growths], rows, first_width=10))

    out.append("\nREVERSE DCF")
    if r.implied_growth is None:
        out.append("  No constant growth rate between -50% and +100% p.a. reproduces the price.")
    else:
        out.append(
            f"  The price of USD {r.quote.price:,.2f} implies constant revenue growth of "
            f"{pct(r.implied_growth)} p.a. for {sc.years} years"
        )
        out.append(
            "  (holding this scenario's margins, reinvestment, WACC and terminal growth fixed)."
        )

    out.append("\nXBRL TAGS USED (latest fiscal year)")
    out.append(sources_lines(latest))
    if r.warnings:
        out.append("\nWARNINGS")
        out.extend(f"  - {msg}" for msg in r.warnings)
    return "\n".join(out)


def render_scenario_summary(results: list[DcfResult]) -> str:
    """One line per scenario: the bull/base/bear range at a glance."""
    rows = [
        [
            r.scenario.name,
            pct(r.scenario.revenue_growth[0]),
            pct(r.scenario.ebit_margin[-1]),
            f"{r.gordon.value_per_share:,.2f}",
            pct(r.gordon.upside),
            f"{r.exit_multiple.value_per_share:,.2f}",
            pct(r.exit_multiple.upside),
        ]
        for r in results
    ]
    header = [
        "Scenario",
        "Growth Y1",
        "Margin YN",
        "Gordon/share",
        "Upside",
        "Exit/share",
        "Upside",
    ]
    price = results[0].quote.price
    return (
        f"SCENARIO SUMMARY (price USD {price:,.2f}, WACC {pct(results[0].wacc.wacc, 2)})\n"
        + table(header, rows, first_width=10)
    )


def export(result: DcfResult, out_dir: str | Path) -> list[Path]:
    """Write projection.csv, sensitivity.csv and summary.json; return the paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tag = f"{result.company.ticker}_{result.scenario.name}"
    written: list[Path] = []

    projection_path = out / f"{tag}_projection.csv"
    with open(projection_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["fiscal_year", "revenue", "growth", "ebit", "nopat", "da", "capex", "dnwc", "ufcf"]
        )
        for row in result.projection:
            writer.writerow(
                [
                    row.fiscal_year,
                    row.revenue,
                    row.revenue_growth,
                    row.ebit,
                    row.nopat,
                    row.depreciation_amortization,
                    row.capex,
                    row.delta_nwc,
                    row.ufcf,
                ]
            )
    written.append(projection_path)

    if result.sensitivity is not None:
        sens_path = out / f"{tag}_sensitivity.csv"
        with open(sens_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["wacc \\ g", *result.sensitivity.growths])
            for wacc, values in zip(
                result.sensitivity.waccs, result.sensitivity.values, strict=True
            ):
                writer.writerow([wacc, *values])
        written.append(sens_path)

    summary = {
        "ticker": result.company.ticker,
        "scenario": result.scenario.name,
        "latest_fiscal_year": result.company.latest.fiscal_year,
        "price": result.quote.price,
        "market_data_source": result.quote.source,
        "wacc": result.wacc.wacc,
        "mid_year_convention": result.mid_year,
        "gordon": {
            "enterprise_value": result.gordon.enterprise_value,
            "equity_value": result.gordon.equity_value,
            "value_per_share": result.gordon.value_per_share,
            "tv_share_of_ev": result.gordon.terminal_share_of_ev,
        },
        "exit_multiple": {
            "enterprise_value": result.exit_multiple.enterprise_value,
            "equity_value": result.exit_multiple.equity_value,
            "value_per_share": result.exit_multiple.value_per_share,
            "tv_share_of_ev": result.exit_multiple.terminal_share_of_ev,
        },
        "implied_exit_multiple": result.checks.implied_exit_multiple,
        "implied_perpetual_growth": result.checks.implied_perpetual_growth,
        "reverse_dcf_implied_growth": result.implied_growth,
        "disclaimer": DISCLAIMER,
    }
    summary_path = out / f"{tag}_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    written.append(summary_path)
    return written

"""Golden test: the full pipeline on the fixture against a valuation rebuilt by hand.

Everything below the fixture loading is recomputed from the raw fixture numbers (in USD
millions) without calling any of the model's own maths, so a regression anywhere in
run_dcf (history ratios, growth/margin paths, NWC, WACC weights, discounting, TV timing,
bridge) changes the result and fails this test.
"""

from pathlib import Path

import pytest

from dcf.assumptions import load_assumptions
from dcf.market import resolve_quote
from dcf.model import company_from_facts, run_dcf

CONFIG = Path(__file__).parents[1] / "config" / "example_offline.yaml"

# Raw fixture values, USD m, FY2023 / FY2024 / FY2025 (the 3-year history window).
REVENUE = [900.0, 1_000.0, 1_100.0]
DA = [44.0, 48.0, 52.0]
CAPEX = [56.0, 60.0, 66.0]
# Operating NWC = receivables + inventory - payables - current deferred revenue
NWC = [110 + 55 - 66 - 22, 120 + 60 - 70 - 25, 130 + 65 - 77 - 28]
LAST_EBIT = 253.0
DEBT, CASH, SHARES = 260.0, 200.0 + 30.0 + 50.0, 99.0  # cash 230 + ST investments 50

# config/example_offline.yaml (base) + src/dcf/defaults.yaml
PRICE, BETA = 30.0, 1.10
RF, ERP, SPREAD, TAX = 0.0498, 0.042, 0.01, 0.21
G1, G_TERMINAL, MARGIN_TARGET, EXIT_MULTIPLE, YEARS = 0.09, 0.025, 0.24, 11.0, 5


def hand_valuation() -> dict[str, float]:
    # WACC with market-value equity weight and book debt
    cost_of_equity = RF + BETA * ERP
    after_tax_debt = (RF + SPREAD) * (1 - TAX)
    equity_mv = PRICE * SHARES
    wacc = (equity_mv * cost_of_equity + DEBT * after_tax_debt) / (equity_mv + DEBT)

    # Ratios from history: 3-year averages, last reported margin
    da_pct = sum(d / r for d, r in zip(DA, REVENUE, strict=True)) / 3
    capex_pct = sum(c / r for c, r in zip(CAPEX, REVENUE, strict=True)) / 3
    nwc_pct = sum(n / r for n, r in zip(NWC, REVENUE, strict=True)) / 3
    margin_start = LAST_EBIT / REVENUE[-1]

    revenue, nwc_prev = REVENUE[-1], NWC[-1]
    pv_explicit = 0.0
    for t in range(1, YEARS + 1):
        growth = G1 + (G_TERMINAL - G1) * (t - 1) / (YEARS - 1)  # linear fade
        margin = margin_start + (MARGIN_TARGET - margin_start) * t / YEARS  # glide
        revenue *= 1 + growth
        ebit = revenue * margin
        nwc = revenue * nwc_pct
        ufcf = ebit * (1 - TAX) + revenue * da_pct - revenue * capex_pct - (nwc - nwc_prev)
        nwc_prev = nwc
        pv_explicit += ufcf / (1 + wacc) ** (t - 0.5)  # mid-year convention
    ebitda = ebit + revenue * da_pct

    tv_gordon = ufcf * (1 + G_TERMINAL) / (wacc - G_TERMINAL)
    ev_gordon = pv_explicit + tv_gordon / (1 + wacc) ** (YEARS - 0.5)
    tv_exit = ebitda * EXIT_MULTIPLE
    ev_exit = pv_explicit + tv_exit / (1 + wacc) ** YEARS
    return {
        "wacc": wacc,
        "gordon_per_share": (ev_gordon - DEBT + CASH) / SHARES,
        "exit_per_share": (ev_exit - DEBT + CASH) / SHARES,
        "implied_multiple": tv_gordon * (1 + wacc) ** 0.5 / ebitda,
    }


def test_fixture_base_case_matches_hand_calculation(company_facts):
    company = company_from_facts("EXMPL", company_facts)
    assumptions = load_assumptions(CONFIG)
    quote = resolve_quote("EXMPL", None, assumptions.market.price, assumptions.market.beta)
    result = run_dcf(company, quote, assumptions, "base", with_sensitivity=False)

    expected = hand_valuation()
    assert result.wacc.wacc == pytest.approx(expected["wacc"], rel=1e-12)
    assert result.gordon.value_per_share == pytest.approx(expected["gordon_per_share"], rel=1e-9)
    assert result.exit_multiple.value_per_share == pytest.approx(
        expected["exit_per_share"], rel=1e-9
    )
    assert result.checks.implied_exit_multiple == pytest.approx(
        expected["implied_multiple"], rel=1e-9
    )
    # The rounded numbers shown in the README example output.
    assert round(expected["wacc"], 6) == 0.092075
    assert round(expected["gordon_per_share"], 2) == 35.76
    assert round(expected["exit_per_share"], 2) == 39.31

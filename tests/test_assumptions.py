"""YAML validation: wrong types and impossible values fail early with the key in the message."""

from pathlib import Path

import pytest

from dcf.assumptions import load_assumptions
from dcf.cli import main
from dcf.financials import summarize
from dcf.market import resolve_quote
from dcf.model import company_from_facts, growth_warnings, run_dcf, select_share_count
from dcf.xbrl import extract_fiscal_years

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "example_offline.yaml"
FIXTURE = str(ROOT / "tests" / "fixtures" / "companyfacts_example.json")


def load(tmp_path, text: str):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(text)
    return load_assumptions(cfg)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("tax_rate: 1.5\n", "tax_rate = 1.5 must be between 0 and 1"),
        ("tax_rate: -0.2\n", "tax_rate = -0.2 must be between 0 and 1"),
        ("scenarios: {base: {exit_multiple: -5}}\n", "exit_multiple = -5.0 must be positive"),
        ("scenarios: {base: {revenue_growth_start: 9}}\n", "looks like a percent"),
        ("scenarios: {base: {revenue_growth_path: [0.1, 9, 0.1]}}\n", r"revenue_growth_path\[1\]"),
        ("scenarios: {base: {ebit_margin_target: 24}}\n", "ebit_margin_target = 24.0"),
        ("scenarios: {base: {terminal_growth: 2.5}}\n", "terminal_growth = 2.5"),
        ("scenarios: {base: {capex_pct_revenue: -0.1}}\n", "must be between 0 and 1"),
        ("wacc: {risk_free_rate: 4.98}\n", "wacc.risk_free_rate = 4.98 looks like a percent"),
        ("wacc: {wacc_override: 0}\n", "wacc_override = 0.0 must be between 0 and 1"),
        ("market: {diluted_shares: 0}\n", "market.diluted_shares must be positive"),
        ("projection: {history_lookback: 0}\n", "history_lookback must be at least 2"),
        ("projection: {history_lookback: 1}\n", "history_lookback must be at least 2"),
        ("balance_sheet: {cash: -1}\n", "balance_sheet.cash must not be negative"),
        ("sensitivity: {wacc_step: 0}\n", "sensitivity steps must be positive"),
    ],
)
def test_impossible_values_are_rejected(tmp_path, text, message):
    with pytest.raises(ValueError, match=message):
        load(tmp_path, text)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('scenarios: {base: {terminal_growth: "abc"}}\n', "scenarios.base.terminal_growth"),
        ("scenarios: {base: {exit_multiple: true}}\n", "exit_multiple must be a number"),
        ("projection: {years: 7.5}\n", "projection.years must be a whole number"),
        ('projection: {mid_year_convention: "yes"}\n', "must be true or false"),
        ("scenarios: {base: {revenue_growth_path: 0.1}}\n", "must be a list of numbers"),
        ("scenarios: {base: 5}\n", "scenarios.base must be a mapping"),
        ("market: 30\n", "market must be a mapping"),
        ("scenarios: {}\n", "At least one scenario"),
    ],
)
def test_wrong_types_are_rejected_with_the_key(tmp_path, text, message):
    with pytest.raises(ValueError, match=message):
        load(tmp_path, text)


def test_numeric_strings_are_accepted(tmp_path):
    # PyYAML reads 1e-2 (no decimal point) as the string "1e-2".
    a = load(tmp_path, "scenarios: {base: {terminal_growth: 1e-2}}\n")
    assert a.scenarios["base"].terminal_growth == 0.01


def test_cli_reports_type_errors_without_traceback(tmp_path, capsys):
    cfg = tmp_path / "abc.yaml"
    cfg.write_text('market: {price: 30, beta: 1.1}\nscenarios: {base: {terminal_growth: "abc"}}\n')
    assert main(["value", "EXMPL", "--offline", "--fixture", FIXTURE, "--config", str(cfg)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: scenarios.base.terminal_growth must be a number")
    assert "Traceback" not in err


def test_zero_share_override_is_not_ignored(company_facts):
    company = company_from_facts("EXMPL", company_facts)
    with pytest.raises(ValueError, match="must be positive"):
        select_share_count(company, override=0.0)


def test_summarize_rejects_lookback_below_two(company_facts):
    years = extract_fiscal_years(company_facts)
    for bad in (0, 1, -1):
        with pytest.raises(ValueError, match=">= 2"):
            summarize(years, bad)
    assert summarize(years, 2).years_used == 2
    # More years requested than available: all four fixture years are used.
    assert summarize(years, 6).years_used == 4


def test_warns_when_fewer_years_than_lookback(company_facts):
    a = load_assumptions(CONFIG, {"projection": {"history_lookback": 6}})
    company = company_from_facts("EXMPL", company_facts)
    quote = resolve_quote("EXMPL", None, a.market.price, a.market.beta)
    result = run_dcf(company, quote, a, with_sensitivity=False)
    assert any("Only 4 fiscal years available" in w for w in result.warnings)


def test_growth_warnings_by_hand():
    # g 3.0%, WACC 4.5%, rf 4.0%: spread 1.5pp, TV = 1.03 / 0.015 = 68.67x UFCF_N.
    (spread_msg,) = growth_warnings(0.03, 0.045, 0.04)
    assert "only 1.50pp" in spread_msg
    assert "69x" in spread_msg
    # g 5.0% above rf 4.0%, but 4pp below the 9.0% WACC: only the ceiling warning.
    (ceiling_msg,) = growth_warnings(0.05, 0.09, 0.04)
    assert "above the risk-free rate (4.00%)" in ceiling_msg
    # The fixture base case (g 2.5%, WACC about 9.2%, rf 4.98%) triggers neither.
    assert growth_warnings(0.025, 0.0921, 0.0498) == []

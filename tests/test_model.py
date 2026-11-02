"""End-to-end valuation on the offline fixture (no network)."""

from pathlib import Path

import pytest

from dcf.assumptions import deep_merge, load_assumptions
from dcf.market import MarketDataError, StaticProvider, resolve_quote
from dcf.model import company_from_facts, gordon_value_per_share, run_dcf
from dcf.projection import with_constant_growth

CONFIG = Path(__file__).parents[1] / "config" / "example_offline.yaml"


@pytest.fixture
def company(company_facts):
    return company_from_facts("EXMPL", company_facts)


@pytest.fixture
def assumptions():
    return load_assumptions(CONFIG)


@pytest.fixture
def quote(assumptions):
    return resolve_quote("EXMPL", None, assumptions.market.price, assumptions.market.beta)


def test_yaml_overrides_merge_onto_defaults(assumptions):
    assert assumptions.market.price == 30.0
    assert assumptions.scenarios["base"].exit_multiple == 11.0
    # User scenarios replace the default set: no default bull shift leaks in.
    assert assumptions.scenarios["bull"].revenue_growth_shift == 0.0
    assert assumptions.wacc.equity_risk_premium == 0.042  # untouched default
    assert assumptions.wacc.cost_of_debt == pytest.approx(
        assumptions.wacc.risk_free_rate + assumptions.wacc.credit_spread
    )


def test_unknown_yaml_key_is_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("wacc:\n  risk_free: 0.04\n")
    with pytest.raises(ValueError, match="Unknown keys"):
        load_assumptions(bad)


def test_deep_merge_keeps_untouched_keys():
    assert deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 3}}) == {"a": {"x": 1, "y": 3}}


def test_market_overrides_win_over_provider():
    provider = StaticProvider(price=50.0, beta=1.3)
    quote = resolve_quote("EXMPL", provider, price_override=None, beta_override=0.9)
    assert (quote.price, quote.beta) == (50.0, 0.9)
    with pytest.raises(MarketDataError):
        resolve_quote("EXMPL", None, price_override=10.0, beta_override=None)


def test_full_run_is_internally_consistent(company, quote, assumptions):
    result = run_dcf(company, quote, assumptions, "base")
    g = result.gordon
    assert g.enterprise_value == pytest.approx(g.pv_explicit + g.pv_terminal)
    # Bridge: equity = EV - debt + cash, from the FY2025 balance sheet of the fixture.
    assert result.bridge.debt == pytest.approx(260e6)
    assert result.bridge.cash == pytest.approx(280e6)
    assert g.equity_value == pytest.approx(g.enterprise_value - 260e6 + 280e6)
    assert g.value_per_share == pytest.approx(g.equity_value / 99e6)
    assert 0 < g.terminal_share_of_ev < 1
    assert result.projection[-1].revenue_growth == pytest.approx(0.025)


def test_sensitivity_center_equals_point_estimate(company, quote, assumptions):
    result = run_dcf(company, quote, assumptions, "base")
    table = result.sensitivity
    mid = len(table.waccs) // 2
    assert table.values[mid][mid] == pytest.approx(result.gordon.value_per_share)


def test_reverse_dcf_round_trip_reprices_to_market(company, quote, assumptions):
    result = run_dcf(company, quote, assumptions, "base")
    assert result.implied_growth is not None
    revalued = gordon_value_per_share(
        with_constant_growth(result.scenario, result.implied_growth),
        result.history,
        result.wacc.wacc,
        result.scenario.terminal_growth,
        result.bridge,
        result.mid_year,
    )
    assert revalued == pytest.approx(quote.price, rel=1e-8)


def test_mid_year_toggle_raises_value(company, quote):
    end_year = load_assumptions(CONFIG, {"projection": {"mid_year_convention": False}})
    mid_year = load_assumptions(CONFIG, {"projection": {"mid_year_convention": True}})
    v_end = run_dcf(company, quote, end_year, with_sensitivity=False).gordon
    v_mid = run_dcf(company, quote, mid_year, with_sensitivity=False).gordon
    assert v_mid.enterprise_value > v_end.enterprise_value


def test_bull_above_base_above_bear(company, quote, assumptions):
    values = {
        name: run_dcf(company, quote, assumptions, name, with_sensitivity=False)
        for name in ("bull", "base", "bear")
    }
    assert (
        values["bull"].gordon.value_per_share
        > values["base"].gordon.value_per_share
        > values["bear"].gordon.value_per_share
    )


def test_wacc_override_is_used(company, quote):
    fixed = load_assumptions(CONFIG, {"wacc": {"wacc_override": 0.08}})
    assert run_dcf(company, quote, fixed, with_sensitivity=False).wacc.wacc == 0.08


def test_unknown_scenario_raises(company, quote, assumptions):
    with pytest.raises(ValueError, match="Unknown scenario"):
        run_dcf(company, quote, assumptions, "moonshot")

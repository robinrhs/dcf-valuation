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


def test_provider_quote_is_validated_too():
    with pytest.raises(ValueError, match="positive"):
        resolve_quote("EXMPL", StaticProvider(price=0.0, beta=1.0))
    with pytest.raises(ValueError, match="plausible"):
        resolve_quote("EXMPL", StaticProvider(price=10.0, beta=float("nan")))


def test_negative_terminal_fcf_gives_na_cross_checks_and_warning(company, quote, tmp_path):
    # Thin margin plus heavy capex: final-year UFCF turns negative.
    cfg = tmp_path / "loss.yaml"
    cfg.write_text(
        "scenarios:\n  base:\n    ebit_margin_target: 0.02\n    capex_pct_revenue_target: 0.15\n"
    )
    result = run_dcf(company, quote, load_assumptions(cfg), with_sensitivity=False)
    assert result.projection[-1].ufcf < 0
    assert result.checks.implied_perpetual_growth is None
    assert any("Final-year FCF is not positive" in w for w in result.warnings)


def test_warns_when_terminal_roic_is_below_wacc(company, quote, tmp_path):
    # Capex far above D&A with a modest margin: lots of reinvestment for little growth.
    cfg = tmp_path / "low_roic.yaml"
    cfg.write_text(
        "scenarios:\n  base:\n    capex_pct_revenue_target: 0.12\n    terminal_growth: 0.02\n"
    )
    result = run_dcf(company, quote, load_assumptions(cfg), with_sensitivity=False)
    roic = result.checks.implied_terminal_roic
    assert roic is not None and roic < result.wacc.wacc
    assert any("below the WACC" in w for w in result.warnings)
    # The default example case earns well above its WACC on new capital: no warning.
    base = run_dcf(company, quote, load_assumptions(CONFIG), with_sensitivity=False)
    assert not any("below the WACC" in w for w in base.warnings)


def test_low_roic_warning_matches_the_sensitivity_grid(company, quote, tmp_path):
    """The warning must not claim that a higher g lowers this model's value.

    The Gordon TV holds UFCF_N fixed, so every grid row rises with g even when the
    implied return on new capital is below the WACC. Only a value-driver TV, where the
    reinvestment grows with g, falls with g in that case.
    """
    cfg = tmp_path / "low_roic.yaml"
    cfg.write_text(
        "scenarios:\n  base:\n    capex_pct_revenue_target: 0.12\n    terminal_growth: 0.02\n"
    )
    result = run_dcf(company, quote, load_assumptions(cfg))
    roic, wacc = result.checks.implied_terminal_roic, result.wacc.wacc
    assert roic is not None and roic < wacc
    for row in result.sensitivity.values:
        assert all(a < b for a, b in zip(row, row[1:], strict=False))
    (message,) = [w for w in result.warnings if "below the WACC" in w]
    assert "lowers the value" not in message
    assert "overstates" in message

    # Value-driver TV at the same RONIC, written out by hand: it falls as g rises.
    final = result.projection[-1]

    def value_driver_tv(g: float) -> float:
        return final.nopat * (1 + g) * (1 - g / roic) / (wacc - g)

    assert value_driver_tv(0.025) < value_driver_tv(0.02) < value_driver_tv(0.015)
    # At the scenario's own g it equals the Gordon TV, because RONIC was implied from it.
    assert value_driver_tv(0.02) == pytest.approx(result.gordon.terminal_value)

import pytest

from dcf.projection import ProjectionYear
from dcf.valuation import (
    BridgeInputs,
    discount_factor,
    equity_value_from_ev,
    exit_multiple_terminal_value,
    gordon_terminal_value,
    implied_exit_multiple,
    implied_perpetual_growth,
    implied_terminal_roic,
    per_share,
    present_value,
    upside,
    value_exit_multiple,
    value_gordon,
)


def flat_projection(ufcf: float = 100.0, ebitda: float = 150.0, years: int = 5):
    """Projection rows with constant cash flow, enough to test discounting maths."""
    return [
        ProjectionYear(
            t=t,
            fiscal_year=2025 + t,
            revenue=1_000.0,
            revenue_growth=0.0,
            ebit_margin=0.12,
            ebit=ebitda - 30.0,
            taxes=0.0,
            nopat=ebitda - 30.0,
            depreciation_amortization=30.0,
            capex=50.0,
            nwc=0.0,
            delta_nwc=0.0,
            ufcf=ufcf,
        )
        for t in range(1, years + 1)
    ]


BRIDGE = BridgeInputs(debt=200.0, cash=50.0, diluted_shares=10.0, share_price=100.0)


def test_discount_factor_end_of_year():
    assert discount_factor(0.10, 1) == pytest.approx(1 / 1.10)
    assert discount_factor(0.10, 2) == pytest.approx(1 / 1.21)


def test_present_value_of_annuity_matches_formula():
    # PV of 100 for 3 years at 10% = 100 * (1 - 1.1^-3) / 0.1
    assert present_value([100.0] * 3, 0.10) == pytest.approx(100 * (1 - 1.1**-3) / 0.1)


def test_mid_year_convention_shifts_every_flow_by_half_a_year():
    flows = [100.0, 120.0, 140.0]
    end = present_value(flows, 0.09, mid_year=False)
    mid = present_value(flows, 0.09, mid_year=True)
    assert mid == pytest.approx(end * 1.09**0.5)
    assert mid > end  # receiving cash earlier is worth more


def test_gordon_growth_formula():
    # 100 * 1.02 / (0.09 - 0.02)
    assert gordon_terminal_value(100.0, 0.09, 0.02) == pytest.approx(102 / 0.07)


def test_gordon_rejects_growth_above_wacc():
    with pytest.raises(ValueError):
        gordon_terminal_value(100.0, 0.05, 0.05)


def test_exit_multiple_terminal_value():
    assert exit_multiple_terminal_value(150.0, 12.0) == pytest.approx(1_800.0)


def test_equity_bridge_arithmetic():
    equity = equity_value_from_ev(1_000.0, debt=200.0, cash=50.0)
    assert equity == pytest.approx(850.0)
    assert per_share(equity, 10.0) == pytest.approx(85.0)
    assert upside(85.0, 100.0) == pytest.approx(-0.15)


def test_gordon_valuation_end_to_end_by_hand():
    projection = flat_projection()
    result = value_gordon(projection, 0.10, 0.02, BRIDGE, mid_year=False)
    pv_explicit = 100 * (1 - 1.1**-5) / 0.1
    tv = 100 * 1.02 / 0.08
    pv_tv = tv / 1.1**5
    assert result.terminal_value == pytest.approx(tv)
    assert result.enterprise_value == pytest.approx(pv_explicit + pv_tv)
    assert result.equity_value == pytest.approx(pv_explicit + pv_tv - 200 + 50)
    assert result.terminal_share_of_ev == pytest.approx(pv_tv / (pv_explicit + pv_tv))


def test_mid_year_gordon_tv_discounted_at_n_minus_half():
    projection = flat_projection()
    result = value_gordon(projection, 0.10, 0.02, BRIDGE, mid_year=True)
    assert result.pv_terminal == pytest.approx(result.terminal_value / 1.1**4.5)


def test_mid_year_exit_tv_discounted_at_n():
    projection = flat_projection()
    result = value_exit_multiple(projection, 0.10, 12.0, BRIDGE, mid_year=True)
    assert result.pv_terminal == pytest.approx(150 * 12 / 1.1**5)


@pytest.mark.parametrize("mid_year", [False, True])
def test_implied_multiple_and_growth_round_trip(mid_year):
    # Convert Gordon TV into an implied exit multiple, then back into a growth rate.
    tv = gordon_terminal_value(100.0, 0.09, 0.025)
    multiple = implied_exit_multiple(tv, 150.0, 0.09, mid_year)
    exit_tv = exit_multiple_terminal_value(150.0, multiple)
    assert implied_perpetual_growth(exit_tv, 100.0, 0.09, mid_year) == pytest.approx(0.025)


def test_same_ev_when_exit_multiple_equals_implied_multiple():
    projection = flat_projection()
    gordon = value_gordon(projection, 0.10, 0.02, BRIDGE, mid_year=True)
    multiple = implied_exit_multiple(gordon.terminal_value, 150.0, 0.10, mid_year=True)
    exit_result = value_exit_multiple(projection, 0.10, multiple, BRIDGE, mid_year=True)
    assert exit_result.enterprise_value == pytest.approx(gordon.enterprise_value)


def test_implied_terminal_roic():
    row = flat_projection()[-1]  # NOPAT 120, reinvestment 50 - 30 = 20 -> rate 1/6
    assert implied_terminal_roic(row, 0.02) == pytest.approx(0.02 * 6)

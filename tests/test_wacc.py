import pytest

from dcf.wacc import (
    WaccInputs,
    after_tax_cost_of_debt,
    capital_weights,
    compute_wacc,
    cost_of_equity_capm,
)


def test_capm_cost_of_equity():
    # 4% + 1.2 * 5% = 10%
    assert cost_of_equity_capm(0.04, 1.2, 0.05) == pytest.approx(0.10)


def test_after_tax_cost_of_debt():
    assert after_tax_cost_of_debt(0.06, 0.25) == pytest.approx(0.045)


def test_market_value_weights_sum_to_one():
    w_e, w_d = capital_weights(market_cap=800.0, debt=200.0)
    assert (w_e, w_d) == pytest.approx((0.8, 0.2))


def test_capital_weights_reject_empty_capital():
    with pytest.raises(ValueError):
        capital_weights(0.0, 0.0)


def test_full_wacc_by_hand():
    inputs = WaccInputs(
        risk_free_rate=0.04,
        beta=1.2,
        equity_risk_premium=0.05,
        pretax_cost_of_debt=0.06,
        tax_rate=0.25,
        market_cap=800.0,
        debt=200.0,
    )
    result = compute_wacc(inputs)
    # 0.8 * 10% + 0.2 * 4.5% = 8.9%
    assert result.cost_of_equity == pytest.approx(0.10)
    assert result.after_tax_cost_of_debt == pytest.approx(0.045)
    assert result.wacc == pytest.approx(0.089)


def test_debt_free_company_wacc_equals_cost_of_equity():
    inputs = WaccInputs(0.04, 1.0, 0.05, 0.06, 0.21, market_cap=1_000.0, debt=0.0)
    result = compute_wacc(inputs)
    assert result.wacc == pytest.approx(result.cost_of_equity)

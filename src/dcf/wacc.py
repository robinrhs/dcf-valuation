"""Weighted average cost of capital (WACC).

Why WACC and not the cost of equity? Unlevered free cash flow is the cash available to
*all* capital providers (debt and equity) before any interest is paid. It must therefore
be discounted at the blended return those providers require, i.e. the WACC. Discounting
it at the cost of equity would mix a "firm" cash flow with an "equity-only" rate.

    Cost of equity (CAPM):  r_e = r_f + beta * ERP
    After-tax cost of debt: r_d * (1 - t)      (interest is tax deductible)
    WACC = E / (D + E) * r_e + D / (D + E) * r_d * (1 - t)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WaccInputs:
    risk_free_rate: float
    beta: float
    equity_risk_premium: float
    pretax_cost_of_debt: float
    tax_rate: float
    market_cap: float  # E: share price * diluted shares (market value of equity)
    debt: float  # D: book value of debt, used as a proxy for its market value


@dataclass(frozen=True)
class WaccResult:
    cost_of_equity: float
    after_tax_cost_of_debt: float
    equity_weight: float
    debt_weight: float
    wacc: float


def cost_of_equity_capm(risk_free_rate: float, beta: float, equity_risk_premium: float) -> float:
    """CAPM: investors demand the risk-free rate plus beta units of market risk premium."""
    return risk_free_rate + beta * equity_risk_premium


def after_tax_cost_of_debt(pretax_cost_of_debt: float, tax_rate: float) -> float:
    """Interest is tax deductible, so the effective cost of debt is r_d * (1 - t)."""
    return pretax_cost_of_debt * (1 - tax_rate)


def capital_weights(market_cap: float, debt: float) -> tuple[float, float]:
    """Market-value weights (E/V, D/V) with V = E + D."""
    total = market_cap + debt
    if total <= 0:
        raise ValueError("Market cap plus debt must be positive")
    return market_cap / total, debt / total


def weighted_average_cost_of_capital(
    cost_of_equity: float, after_tax_debt_cost: float, equity_weight: float, debt_weight: float
) -> float:
    """WACC = E/V * r_e + D/V * r_d * (1 - t)."""
    return equity_weight * cost_of_equity + debt_weight * after_tax_debt_cost


def compute_wacc(inputs: WaccInputs) -> WaccResult:
    """Full WACC build-up from raw inputs."""
    r_e = cost_of_equity_capm(inputs.risk_free_rate, inputs.beta, inputs.equity_risk_premium)
    r_d = after_tax_cost_of_debt(inputs.pretax_cost_of_debt, inputs.tax_rate)
    w_e, w_d = capital_weights(inputs.market_cap, inputs.debt)
    return WaccResult(
        cost_of_equity=r_e,
        after_tax_cost_of_debt=r_d,
        equity_weight=w_e,
        debt_weight=w_d,
        wacc=weighted_average_cost_of_capital(r_e, r_d, w_e, w_d),
    )

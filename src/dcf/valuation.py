"""Discounting, terminal value and the bridge from enterprise value to value per share.

    Enterprise value  EV = sum_t UFCF_t * DF_t  +  TV * DF_TV
    Discount factor   DF_t = 1 / (1 + WACC)^t           (end-of-year convention)
                      DF_t = 1 / (1 + WACC)^(t - 0.5)   (mid-year convention)

Mid-year convention: cash does not arrive in one lump on 31 December but roughly evenly
over the year, so on average half a year earlier. Discounting by t - 0.5 captures that.

Terminal value (TV) at the end of the final explicit year N, two methods:
    Gordon growth:  TV = UFCF_N * (1 + g) / (WACC - g)
    Exit multiple:  TV = EBITDA_N * (EV/EBITDA multiple)

Timing of the TV under the mid-year convention: the Gordon formula values a stream of
cash flows that themselves arrive mid-year, so it is discounted with N - 0.5. An exit
multiple represents a sale price at the end of year N, so it is discounted with N.

Equity bridge:
    Equity value = EV - debt - finance-lease liabilities + cash (and short-term investments)
    Value per share = equity value / diluted shares
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from dcf.projection import ProjectionYear

TerminalMethod = Literal["gordon", "exit_multiple"]


@dataclass(frozen=True)
class BridgeInputs:
    debt: float  # financial debt (bonds, loans, commercial paper)
    cash: float  # cash and equivalents plus short-term investments
    diluted_shares: float
    share_price: float
    lease_liabilities: float = 0.0  # finance leases, treated as debt-like claims
    shares_source: str = "weighted-average diluted shares (10-K)"  # shown in the report

    @property
    def debt_like(self) -> float:
        """All claims of lenders that rank ahead of equity: debt plus finance leases."""
        return self.debt + self.lease_liabilities


@dataclass(frozen=True)
class ValuationResult:
    method: TerminalMethod
    terminal_value: float
    pv_explicit: float  # present value of the explicit-period cash flows
    pv_terminal: float
    enterprise_value: float
    equity_value: float
    value_per_share: float
    upside: float  # value per share / price - 1

    @property
    def terminal_share_of_ev(self) -> float:
        """How much of EV comes from the terminal value (often 60-80%+)."""
        return self.pv_terminal / self.enterprise_value


# ------------------------------------------------------------------ discounting
def discount_factor(wacc: float, t: float, mid_year: bool = False) -> float:
    """Present value of 1 dollar received in year t (minus half a year if mid-year)."""
    exponent = t - 0.5 if mid_year else t
    return 1 / (1 + wacc) ** exponent


def present_value(cash_flows: list[float], wacc: float, mid_year: bool = False) -> float:
    """Sum of discounted cash flows for years 1..N."""
    return sum(cf * discount_factor(wacc, t, mid_year) for t, cf in enumerate(cash_flows, start=1))


def terminal_discount_factor(
    wacc: float, years: int, method: TerminalMethod, mid_year: bool
) -> float:
    """Gordon TV follows the mid-year timing of the cash flows; an exit is end of year N."""
    shift_half_year = mid_year and method == "gordon"
    return discount_factor(wacc, years, mid_year=shift_half_year)


# ------------------------------------------------------------------ terminal value
def gordon_terminal_value(final_fcf: float, wacc: float, growth: float) -> float:
    """Value at year N of all cash flows from N+1 onwards, growing at g forever."""
    if wacc <= growth:
        raise ValueError("WACC must exceed terminal growth for the Gordon growth model")
    return final_fcf * (1 + growth) / (wacc - growth)


def exit_multiple_terminal_value(final_ebitda: float, multiple: float) -> float:
    """Value at year N if the business were sold at an EV/EBITDA multiple."""
    return final_ebitda * multiple


def implied_exit_multiple(
    gordon_tv: float, final_ebitda: float, wacc: float, mid_year: bool
) -> float | None:
    """EV/EBITDA multiple that the Gordon TV implies at the end of year N.

    Under the mid-year convention the Gordon TV is discounted half a year less, so its
    end-of-year-N equivalent is TV * (1 + WACC)^0.5.
    Returns None if EBITDA_N or the Gordon TV is not positive: a multiple of a negative
    number has no economic meaning.
    """
    if final_ebitda <= 0 or gordon_tv <= 0:
        return None
    tv_at_n = gordon_tv * (1 + wacc) ** 0.5 if mid_year else gordon_tv
    return tv_at_n / final_ebitda


def implied_perpetual_growth(
    exit_tv: float, final_fcf: float, wacc: float, mid_year: bool
) -> float | None:
    """Perpetual growth rate that would make the Gordon TV equal the exit-multiple TV.

    Solve TV = FCF * (1 + g) / (WACC - g) for g:  g = (TV * WACC - FCF) / (TV + FCF).
    Under the mid-year convention the Gordon TV sits half a year earlier than the exit
    TV, so the exit TV is first moved to that date: TV / (1 + WACC)^0.5.
    Returns None if UFCF_N or the exit TV is not positive: no growth rate turns a negative
    cash flow into a positive sale price.
    """
    if final_fcf <= 0 or exit_tv <= 0:
        return None
    tv = exit_tv / (1 + wacc) ** 0.5 if mid_year else exit_tv
    return (tv * wacc - final_fcf) / (tv + final_fcf)


def implied_terminal_roic(final_year: ProjectionYear, growth: float) -> float | None:
    """Return on new invested capital implied by the final year's reinvestment.

    Steady-state identity: g = reinvestment rate * ROIC, with
    reinvestment rate = (capex - D&A + dNWC) / NOPAT. A very high implied ROIC means the
    model grows forever with very little reinvestment - a warning sign.
    """
    reinvestment = final_year.capex - final_year.depreciation_amortization + final_year.delta_nwc
    if final_year.nopat <= 0 or reinvestment <= 0:
        return None
    return growth / (reinvestment / final_year.nopat)


# ------------------------------------------------------------------ bridge
def equity_value_from_ev(
    enterprise_value: float, debt: float, cash: float, lease_liabilities: float = 0.0
) -> float:
    """EV belongs to all capital providers: subtract debt and debt-like finance leases,
    add back non-operating cash."""
    return enterprise_value - debt - lease_liabilities + cash


def per_share(equity_value: float, diluted_shares: float) -> float:
    if diluted_shares <= 0:
        raise ValueError("Diluted shares must be positive")
    return equity_value / diluted_shares


def upside(value_per_share: float, share_price: float) -> float:
    """Positive = model value above market price (upside), negative = downside."""
    return value_per_share / share_price - 1


# ------------------------------------------------------------------ full valuation
def value_firm(
    projection: list[ProjectionYear],
    wacc: float,
    terminal_value: float,
    method: TerminalMethod,
    bridge: BridgeInputs,
    mid_year: bool,
) -> ValuationResult:
    """Discount explicit cash flows and a given TV, then run the equity bridge."""
    years = len(projection)
    pv_explicit = present_value([row.ufcf for row in projection], wacc, mid_year)
    pv_terminal = terminal_value * terminal_discount_factor(wacc, years, method, mid_year)
    ev = pv_explicit + pv_terminal
    equity = equity_value_from_ev(ev, bridge.debt, bridge.cash, bridge.lease_liabilities)
    value = per_share(equity, bridge.diluted_shares)
    return ValuationResult(
        method=method,
        terminal_value=terminal_value,
        pv_explicit=pv_explicit,
        pv_terminal=pv_terminal,
        enterprise_value=ev,
        equity_value=equity,
        value_per_share=value,
        upside=upside(value, bridge.share_price),
    )


def value_gordon(
    projection: list[ProjectionYear],
    wacc: float,
    terminal_growth: float,
    bridge: BridgeInputs,
    mid_year: bool,
) -> ValuationResult:
    tv = gordon_terminal_value(projection[-1].ufcf, wacc, terminal_growth)
    return value_firm(projection, wacc, tv, "gordon", bridge, mid_year)


def value_exit_multiple(
    projection: list[ProjectionYear],
    wacc: float,
    multiple: float,
    bridge: BridgeInputs,
    mid_year: bool,
) -> ValuationResult:
    tv = exit_multiple_terminal_value(projection[-1].ebitda, multiple)
    return value_firm(projection, wacc, tv, "exit_multiple", bridge, mid_year)

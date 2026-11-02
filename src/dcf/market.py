"""Market inputs (share price, beta) behind a small, swappable interface.

SEC filings contain accounting data but no market prices. Price and beta come from a
``MarketDataProvider``. The default implementation uses yfinance (unofficial Yahoo
Finance data); any other source (Bloomberg export, a CSV, a broker API) only needs to
implement ``get_quote``. Values set in the YAML file always win over the provider.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import date
from typing import Protocol


class MarketDataError(RuntimeError):
    """Raised when price or beta cannot be obtained."""


@dataclass(frozen=True)
class MarketQuote:
    ticker: str
    price: float
    beta: float
    source: str  # where the numbers came from, shown in the report
    as_of: str


class MarketDataProvider(Protocol):
    def get_quote(self, ticker: str) -> MarketQuote: ...


class YFinanceProvider:
    """Last price and Yahoo's beta (5-year monthly returns vs. the S&P 500)."""

    def get_quote(self, ticker: str) -> MarketQuote:
        try:
            import yfinance as yf  # imported lazily: only needed for live market data
        except ImportError as exc:  # pragma: no cover - yfinance is a dependency
            raise MarketDataError("yfinance is not installed") from exc
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # silence pandas deprecation noise
                handle = yf.Ticker(ticker)
                price = handle.fast_info["last_price"]
                beta = handle.info.get("beta")
        except Exception as exc:  # network errors, rate limits, unknown tickers
            raise MarketDataError(f"yfinance lookup failed for {ticker}: {exc}") from exc
        if price is None or beta is None:
            raise MarketDataError(f"yfinance returned no price/beta for {ticker}")
        return MarketQuote(ticker, float(price), float(beta), "yfinance", date.today().isoformat())


class StaticProvider:
    """Fixed numbers - used for offline runs and tests."""

    def __init__(self, price: float, beta: float, source: str = "static") -> None:
        self.price = price
        self.beta = beta
        self.source = source

    def get_quote(self, ticker: str) -> MarketQuote:
        return MarketQuote(ticker, self.price, self.beta, self.source, date.today().isoformat())


def resolve_quote(
    ticker: str,
    provider: MarketDataProvider | None,
    price_override: float | None = None,
    beta_override: float | None = None,
) -> MarketQuote:
    """Combine YAML overrides with provider data. Overrides always take precedence.

    If both price and beta are overridden, the provider is never called (fully offline).
    """
    if price_override is not None and beta_override is not None:
        return MarketQuote(
            ticker, price_override, beta_override, "manual override", date.today().isoformat()
        )
    if provider is None:
        raise MarketDataError(
            "No market data provider available: set market.price and market.beta in YAML "
            "(or pass --price/--beta)"
        )
    quote = provider.get_quote(ticker)
    source = quote.source
    if price_override is not None or beta_override is not None:
        source += " + manual override"
    return MarketQuote(
        ticker=ticker,
        price=price_override if price_override is not None else quote.price,
        beta=beta_override if beta_override is not None else quote.beta,
        source=source,
        as_of=quote.as_of,
    )

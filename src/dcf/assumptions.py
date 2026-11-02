"""Load assumptions from YAML: packaged defaults, deep-merged with a user file."""

from __future__ import annotations

import copy
import datetime as dt
import math
from dataclasses import dataclass, fields
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from dcf.projection import ScenarioSpec


@dataclass(frozen=True)
class MarketOverrides:
    price: float | None = None
    beta: float | None = None
    diluted_shares: float | None = None
    as_of: str | None = None  # date of a manually entered price / beta, e.g. 2026-11-02


@dataclass(frozen=True)
class WaccAssumptions:
    risk_free_rate: float
    equity_risk_premium: float
    credit_spread: float
    pretax_cost_of_debt: float | None = None
    wacc_override: float | None = None

    @property
    def cost_of_debt(self) -> float:
        """Pre-tax cost of debt: explicit value, else risk-free rate + credit spread."""
        if self.pretax_cost_of_debt is not None:
            return self.pretax_cost_of_debt
        return self.risk_free_rate + self.credit_spread


@dataclass(frozen=True)
class ProjectionSettings:
    years: int = 5
    mid_year_convention: bool = True
    history_lookback: int = 3


@dataclass(frozen=True)
class BalanceSheetOverrides:
    debt: float | None = None
    cash: float | None = None
    finance_lease_liabilities: float | None = None
    # True: finance leases count as debt in the bridge and WACC, and lease-financed assets
    # as capex. False: both are ignored (the pre-ASC 842 "lease = rent" view).
    finance_leases_as_debt: bool = True


@dataclass(frozen=True)
class SensitivitySettings:
    wacc_step: float = 0.005
    growth_step: float = 0.005
    steps_each_side: int = 2


@dataclass(frozen=True)
class Assumptions:
    market: MarketOverrides
    wacc: WaccAssumptions
    tax_rate: float
    projection: ProjectionSettings
    balance_sheet: BalanceSheetOverrides
    sensitivity: SensitivitySettings
    scenarios: dict[str, ScenarioSpec]


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively replace keys of ``base`` with those in ``override``."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def default_assumptions_dict() -> dict:
    text = resources.files("dcf").joinpath("defaults.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def _number(where: str, value: Any) -> float:
    """A finite float. Numeric strings are accepted (PyYAML reads ``1e-2`` as a string)."""
    if isinstance(value, bool):
        raise ValueError(f"{where} must be a number, got {value!r}")
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            raise ValueError(
                f"{where} must be a number (decimals: 0.05 = 5%), got {value!r}"
            ) from None
    if not isinstance(value, int | float):
        raise ValueError(f"{where} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{where} must be a finite number, got {value!r}")
    return float(value)


def _coerce(where: str, type_name: str, value: Any) -> Any:
    """Check one YAML value against the dataclass field type and convert it.

    Without this, a typo such as ``terminal_growth: "abc"`` only fails deep inside the
    maths with a raw TypeError instead of a message naming the key.
    """
    optional = type_name.endswith("| None")
    base = type_name.removesuffix("| None").strip()
    if value is None:
        if optional:
            return None
        raise ValueError(f"{where} needs a value")
    if base == "float":
        return _number(where, value)
    if base == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{where} must be a whole number, got {value!r}")
        return value
    if base == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{where} must be true or false, got {value!r}")
        return value
    if base == "str":
        if isinstance(value, dt.date):  # YAML turns 2026-11-02 into a date
            return value.isoformat()
        if not isinstance(value, str):
            raise ValueError(f"{where} must be text, got {value!r}")
        return value
    if base.startswith("tuple"):
        if not isinstance(value, list | tuple):
            raise ValueError(f"{where} must be a list of numbers, got {value!r}")
        return tuple(_number(f"{where}[{i}]", v) for i, v in enumerate(value))
    return value  # pragma: no cover - every field type above is handled


def _build(cls: type, data: Any, where: str) -> Any:
    """Create a dataclass from a dict, rejecting unknown keys (catches YAML typos)."""
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise ValueError(f"{where} must be a mapping of keys to values, got {data!r}")
    allowed = {f.name: f.type for f in fields(cls)}
    unknown = set(data) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown keys for {cls.__name__}: {sorted(unknown)}")
    values = {key: _coerce(f"{where}.{key}", allowed[key], value) for key, value in data.items()}
    return cls(**values)


def _scenario(name: str, data: Any) -> ScenarioSpec:
    if data is not None and not isinstance(data, dict):
        raise ValueError(f"scenarios.{name} must be a mapping of keys to values, got {data!r}")
    return _build(ScenarioSpec, {**(data or {}), "name": str(name)}, f"scenarios.{name}")


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _check_rate(where: str, value: float | None) -> None:
    """Rates are decimals. |rate| >= 1 almost always means a percent typed as 5 for 5%."""
    if value is not None:
        _check(abs(value) < 1, f"{where} = {value} looks like a percent: use decimals (0.05 = 5%)")


def _check_ratio(where: str, value: float | None) -> None:
    """Share of revenue between 0 and 1 (D&A, capex)."""
    if value is not None:
        _check(0 <= value < 1, f"{where} = {value} must be between 0 and 1 (0.05 = 5%)")


def validate_scenario(spec: ScenarioSpec) -> None:
    where = f"scenarios.{spec.name}"
    for key in (
        "revenue_growth_start",
        "revenue_growth_shift",
        "ebit_margin_start",
        "ebit_margin_target",
        "ebit_margin_shift",
        "nwc_pct_revenue",
        "terminal_growth",
    ):
        _check_rate(f"{where}.{key}", getattr(spec, key))
    for i, growth in enumerate(spec.revenue_growth_path or ()):
        _check_rate(f"{where}.revenue_growth_path[{i}]", growth)
    for key in (
        "da_pct_revenue",
        "da_pct_revenue_target",
        "capex_pct_revenue",
        "capex_pct_revenue_target",
    ):
        _check_ratio(f"{where}.{key}", getattr(spec, key))
    _check(
        spec.exit_multiple > 0,
        f"{where}.exit_multiple = {spec.exit_multiple} must be positive (EV / EBITDA)",
    )


def validate(assumptions: Assumptions) -> None:
    """Reject inputs that are economically impossible or most likely typos."""
    a = assumptions
    _check(0 <= a.tax_rate < 1, f"tax_rate = {a.tax_rate} must be between 0 and 1 (0.21 = 21%)")
    w = a.wacc
    for key in ("risk_free_rate", "equity_risk_premium", "credit_spread", "pretax_cost_of_debt"):
        _check_rate(f"wacc.{key}", getattr(w, key))
    if w.wacc_override is not None:
        _check(
            0 < w.wacc_override < 1,
            f"wacc.wacc_override = {w.wacc_override} must be between 0 and 1 (0.09 = 9%)",
        )
    if a.market.as_of is not None:
        try:
            dt.date.fromisoformat(a.market.as_of)
        except ValueError:
            raise ValueError(
                f"market.as_of = {a.market.as_of!r} must be a date in the form YYYY-MM-DD"
            ) from None
    if a.market.diluted_shares is not None:
        _check(a.market.diluted_shares > 0, "market.diluted_shares must be positive")
    _check(
        a.projection.history_lookback >= 2,
        "projection.history_lookback must be at least 2 fiscal years (one growth period)",
    )
    b = a.balance_sheet
    for key in ("debt", "cash", "finance_lease_liabilities"):
        value = getattr(b, key)
        if value is not None:
            _check(value >= 0, f"balance_sheet.{key} must not be negative")
    s = a.sensitivity
    _check(s.wacc_step > 0 and s.growth_step > 0, "sensitivity steps must be positive")
    _check(s.steps_each_side >= 0, "sensitivity.steps_each_side must not be negative")
    for spec in a.scenarios.values():
        validate_scenario(spec)


def assumptions_from_dict(data: dict) -> Assumptions:
    raw_scenarios = data.get("scenarios")
    if not isinstance(raw_scenarios, dict) or not raw_scenarios:
        raise ValueError("At least one scenario must be defined")
    scenarios = {name: _scenario(name, spec) for name, spec in raw_scenarios.items()}
    assumptions = Assumptions(
        market=_build(MarketOverrides, data.get("market"), "market"),
        wacc=_build(WaccAssumptions, data["wacc"], "wacc"),
        tax_rate=_number("tax_rate", data["tax_rate"]),
        projection=_build(ProjectionSettings, data.get("projection"), "projection"),
        balance_sheet=_build(BalanceSheetOverrides, data.get("balance_sheet"), "balance_sheet"),
        sensitivity=_build(SensitivitySettings, data.get("sensitivity"), "sensitivity"),
        scenarios=scenarios,
    )
    validate(assumptions)
    return assumptions


def load_assumptions(path: str | Path | None = None, overrides: dict | None = None) -> Assumptions:
    """Defaults <- user YAML file <- programmatic overrides (e.g. from CLI flags).

    Everything is deep-merged key by key, except ``scenarios``: if the user file defines
    scenarios, they replace the default set completely, so no default bull/bear shift can
    silently leak into a company-specific case.
    """
    data = default_assumptions_dict()
    if path is not None:
        with open(path, encoding="utf-8") as handle:
            user = yaml.safe_load(handle) or {}
        data = deep_merge(data, user)
        if "scenarios" in user:
            data["scenarios"] = copy.deepcopy(user["scenarios"])
    if overrides:
        data = deep_merge(data, overrides)
    return assumptions_from_dict(data)

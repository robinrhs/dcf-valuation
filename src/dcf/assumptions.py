"""Load assumptions from YAML: packaged defaults, deep-merged with a user file."""

from __future__ import annotations

import copy
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


def _build(cls: type, data: dict | None) -> Any:
    """Create a dataclass from a dict, rejecting unknown keys (catches YAML typos)."""
    data = data or {}
    allowed = {f.name for f in fields(cls)}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unknown keys for {cls.__name__}: {sorted(unknown)}")
    return cls(**data)


def _scenario(name: str, data: dict) -> ScenarioSpec:
    data = dict(data or {})
    if data.get("revenue_growth_path") is not None:
        data["revenue_growth_path"] = tuple(float(g) for g in data["revenue_growth_path"])
    return _build(ScenarioSpec, {"name": name, **data})


def assumptions_from_dict(data: dict) -> Assumptions:
    scenarios = {name: _scenario(name, spec) for name, spec in data["scenarios"].items()}
    if not scenarios:
        raise ValueError("At least one scenario must be defined")
    return Assumptions(
        market=_build(MarketOverrides, data.get("market")),
        wacc=_build(WaccAssumptions, data["wacc"]),
        tax_rate=float(data["tax_rate"]),
        projection=_build(ProjectionSettings, data.get("projection")),
        balance_sheet=_build(BalanceSheetOverrides, data.get("balance_sheet")),
        sensitivity=_build(SensitivitySettings, data.get("sensitivity")),
        scenarios=scenarios,
    )


def load_assumptions(path: str | Path | None = None, overrides: dict | None = None) -> Assumptions:
    """Defaults <- user YAML file <- programmatic overrides (e.g. from CLI flags)."""
    data = default_assumptions_dict()
    if path is not None:
        with open(path, encoding="utf-8") as handle:
            data = deep_merge(data, yaml.safe_load(handle) or {})
    if overrides:
        data = deep_merge(data, overrides)
    return assumptions_from_dict(data)

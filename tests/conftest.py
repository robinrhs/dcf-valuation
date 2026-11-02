from pathlib import Path

import pytest

from dcf.sec_client import load_company_facts_file

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_FILE = FIXTURE_DIR / "companyfacts_example.json"


@pytest.fixture
def company_facts() -> dict:
    """Hand-made, synthetic companyfacts JSON (no network)."""
    return load_company_facts_file(FIXTURE_FILE)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail loudly if any test tries to reach the internet."""

    def _blocked(*args, **kwargs):
        raise AssertionError("Network access is not allowed in tests")

    monkeypatch.setattr("requests.get", _blocked)
    monkeypatch.setattr("requests.Session.request", _blocked)


@pytest.fixture
def add_fact():
    """Helper to append facts to a companyfacts dict (small variants of the fixture)."""
    return add_annual_fact


def add_annual_fact(
    facts: dict,
    tag: str,
    end: str,
    val: float,
    start: str | None = None,
    unit: str = "USD",
    taxonomy: str = "us-gaap",
    form: str = "10-K",
    fp: str = "FY",
    filed: str = "2026-02-11",
) -> None:
    """Append one fact to a companyfacts dict (to build small variants of the fixture)."""
    fact = {"end": end, "val": val, "accn": "0000000000-26-000010", "fy": 2025, "fp": fp}
    fact.update({"form": form, "filed": filed})
    if start is not None:
        fact["start"] = start
    units = facts["facts"].setdefault(taxonomy, {}).setdefault(tag, {"units": {}})["units"]
    units.setdefault(unit, []).append(fact)

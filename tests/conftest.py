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

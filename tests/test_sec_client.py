"""SecClient download path with a fake ``requests.get`` (no network)."""

import pytest

from dcf import sec_client
from dcf.sec_client import SecClient, SecError

UA = "Jane Doe jane@example.org"


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def no_sleep(monkeypatch):
    """Record back-off waits instead of sleeping."""
    waits: list[float] = []
    monkeypatch.setattr(sec_client.time, "sleep", waits.append)
    return waits


def fake_get(monkeypatch, responses: list[FakeResponse]) -> list[dict]:
    """Replace requests.get with a stub returning ``responses`` in order; log each call."""
    calls: list[dict] = []

    def _get(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers, "timeout": timeout})
        return responses[len(calls) - 1]

    monkeypatch.setattr(sec_client.requests, "get", _get)
    return calls


def test_download_sends_user_agent_and_writes_cache(tmp_path, monkeypatch, no_sleep):
    tickers = {"0": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"}}
    calls = fake_get(monkeypatch, [FakeResponse(200, tickers)])
    client = SecClient(cache_dir=tmp_path, user_agent=UA)
    assert client.ticker_to_cik("msft") == (789019, "MICROSOFT CORP")
    assert calls[0]["url"] == sec_client.TICKERS_URL
    assert calls[0]["headers"]["User-Agent"] == UA
    assert (tmp_path / "company_tickers.json").exists()
    # Second lookup is served from the cache: no new request.
    client.ticker_to_cik("MSFT")
    assert len(calls) == 1


def test_429_is_retried_with_back_off(tmp_path, monkeypatch, no_sleep):
    calls = fake_get(monkeypatch, [FakeResponse(429), FakeResponse(200, {"facts": {}})])
    client = SecClient(cache_dir=tmp_path, user_agent=UA)
    assert client.company_facts(789019) == {"facts": {}}
    assert len(calls) == 2
    assert calls[0]["url"].endswith("CIK0000789019.json")
    assert 2 in no_sleep  # waited 2 seconds before the retry


def test_persistent_error_raises_sec_error(tmp_path, monkeypatch, no_sleep):
    fake_get(monkeypatch, [FakeResponse(503), FakeResponse(503), FakeResponse(503)])
    client = SecClient(cache_dir=tmp_path, user_agent=UA)
    with pytest.raises(SecError, match="HTTP 503"):
        client.company_facts(1)


def test_throttle_keeps_requests_apart(monkeypatch, no_sleep):
    clock = iter([100.0, 100.0, 100.05, 100.25])
    monkeypatch.setattr(sec_client.time, "monotonic", lambda: next(clock))
    client = SecClient(user_agent=UA)
    client._throttle()  # first request: no wait
    client._throttle()  # 0.05 s later: wait the remaining 0.15 s
    assert no_sleep == [pytest.approx(0.15)]


def test_refuses_to_download_without_user_agent(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    calls = fake_get(monkeypatch, [])
    client = SecClient(cache_dir=tmp_path)
    with pytest.raises(SecError, match="SEC_USER_AGENT is not set"):
        client.company_facts(789019)
    assert calls == []  # nothing was sent

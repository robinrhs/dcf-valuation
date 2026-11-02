"""Download data from the official SEC EDGAR APIs, with a local cache.

Endpoints used (both free, no API key):
- https://www.sec.gov/files/company_tickers.json      ticker -> CIK (company id)
- https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json   all XBRL facts of a filer

SEC fair-access rules: identify yourself with a User-Agent of the form "Name email" and
stay below 10 requests per second. Set your own identity via the SEC_USER_AGENT
environment variable; the default below is a neutral placeholder.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

DEFAULT_USER_AGENT = "dcf-valuation research-tool admin@example.com"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# 5 requests/second keeps us comfortably below the SEC limit of 10/second.
MIN_SECONDS_BETWEEN_REQUESTS = 0.2
DEFAULT_CACHE_DIR = Path(".cache") / "sec"
DEFAULT_MAX_AGE_DAYS = 7.0


class SecError(RuntimeError):
    """Raised when EDGAR data cannot be obtained (network, unknown ticker, offline miss)."""


def sec_user_agent() -> str:
    """User-Agent sent to the SEC: $SEC_USER_AGENT if set, otherwise the placeholder."""
    return os.environ.get("SEC_USER_AGENT", "").strip() or DEFAULT_USER_AGENT


def load_company_facts_file(path: str | Path) -> dict:
    """Read a companyfacts JSON file from disk (used for offline fixtures)."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class SecClient:
    """Small EDGAR client: polite rate limiting plus a file cache."""

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        user_agent: str | None = None,
        max_age_days: float = DEFAULT_MAX_AGE_DAYS,
        offline: bool = False,
        refresh: bool = False,
    ) -> None:
        self.cache_dir = Path(cache_dir or os.environ.get("DCF_CACHE_DIR") or DEFAULT_CACHE_DIR)
        self.user_agent = user_agent or sec_user_agent()
        self.max_age_seconds = max_age_days * 86_400
        self.offline = offline
        self.refresh = refresh
        self._last_request = 0.0
        self._warned_placeholder = False

    # ----------------------------------------------------------------- public API
    def ticker_to_cik(self, ticker: str) -> tuple[int, str]:
        """Return (CIK, company name) for a ticker such as "MSFT"."""
        table = self._get_json(TICKERS_URL, "company_tickers.json")
        wanted = ticker.upper().replace(".", "-")
        for row in table.values():
            if str(row["ticker"]).upper() == wanted:
                return int(row["cik_str"]), str(row["title"])
        raise SecError(f"Ticker {ticker!r} not found in SEC company_tickers.json")

    def company_facts(self, cik: int) -> dict:
        """All XBRL facts reported by a company (companyfacts API)."""
        return self._get_json(COMPANY_FACTS_URL.format(cik=cik), f"companyfacts_CIK{cik:010d}.json")

    # ----------------------------------------------------------------- internals
    def _cache_is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        if self.offline:
            return True  # offline: any cached copy is better than nothing
        age = time.time() - path.stat().st_mtime
        return not self.refresh and age < self.max_age_seconds

    def _get_json(self, url: str, cache_name: str) -> dict:
        path = self.cache_dir / cache_name
        if self._cache_is_fresh(path):
            return json.loads(path.read_text(encoding="utf-8"))
        if self.offline:
            raise SecError(f"Offline mode and no cached copy of {url} in {self.cache_dir}")
        data = self._download(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def _throttle(self) -> None:
        wait = MIN_SECONDS_BETWEEN_REQUESTS - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _download(self, url: str, retries: int = 3) -> dict:
        if self.user_agent == DEFAULT_USER_AGENT and not self._warned_placeholder:
            print(
                "note: using the placeholder SEC User-Agent. Please set SEC_USER_AGENT="
                '"Your Name your@email" as required by the SEC fair-access policy.',
                file=sys.stderr,
            )
            self._warned_placeholder = True
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        for attempt in range(retries):
            self._throttle()
            try:
                response = requests.get(url, headers=headers, timeout=30)
            except requests.RequestException as exc:
                if attempt == retries - 1:
                    raise SecError(f"Could not reach {url}: {exc}") from exc
                time.sleep(2**attempt)
                continue
            if response.status_code == 200:
                return response.json()
            if response.status_code in (429, 503) and attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))  # back off when the SEC asks us to slow down
                continue
            raise SecError(f"SEC returned HTTP {response.status_code} for {url}")
        raise SecError(f"Giving up on {url}")

import json

import pytest

from dcf.sec_client import SecClient, SecError, sec_user_agent
from dcf.xbrl import annual_values, extract_fiscal_years, is_annual_fact, missing_items

M = 1_000_000


def test_extracts_four_fiscal_years_oldest_first(company_facts):
    years = extract_fiscal_years(company_facts)
    assert [y.fiscal_year for y in years] == [2022, 2023, 2024, 2025]
    assert years[-1].period_end == "2025-12-31"


def test_revenue_tag_fallback_is_applied_per_year(company_facts):
    # FY2022 only exists under the old "Revenues" tag, later years under the ASC 606 tag.
    years = {y.fiscal_year: y for y in extract_fiscal_years(company_facts)}
    assert years[2022].revenue == 800 * M
    assert years[2022].sources["revenue"] == "Revenues"
    assert years[2025].revenue == 1100 * M
    assert years[2025].sources["revenue"] == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_da_is_rebuilt_from_depreciation_plus_amortization(company_facts):
    latest = extract_fiscal_years(company_facts)[-1]
    assert latest.depreciation_amortization == (42 + 10) * M
    assert latest.sources["depreciation_amortization"] == (
        "Depreciation + AmortizationOfIntangibleAssets"
    )


def test_debt_is_rebuilt_from_noncurrent_plus_current(company_facts):
    latest = extract_fiscal_years(company_facts)[-1]
    assert latest.long_term_debt == (240 + 20) * M
    assert latest.short_term_borrowings is None  # not reported -> treated as zero
    assert latest.total_debt == 260 * M


def test_restated_value_from_latest_filing_wins(company_facts):
    years = {y.fiscal_year: y for y in extract_fiscal_years(company_facts)}
    assert years[2024].ebit == 220 * M  # originally filed as 218m, restated a year later


def test_quarterly_and_10q_facts_are_ignored(company_facts):
    values = annual_values(
        company_facts, "RevenueFromContractWithCustomerExcludingAssessedTax", "duration", "USD"
    )
    assert values["2025-12-31"] == 1100 * M  # not the 290m Q4 fact with the same end date
    assert "2025-03-31" not in values  # 10-Q fact excluded


def test_is_annual_fact_rules():
    base = {"form": "10-K", "fp": "FY", "end": "2025-12-31"}
    assert is_annual_fact({**base, "start": "2025-01-01"}, "duration")
    assert not is_annual_fact({**base, "start": "2025-10-01"}, "duration")  # a quarter
    assert is_annual_fact(base, "instant")
    assert not is_annual_fact({**base, "form": "10-Q"}, "instant")


def test_missing_items_and_derived_balances(company_facts):
    years = {y.fiscal_year: y for y in extract_fiscal_years(company_facts)}
    fy2022 = years[2022]
    assert "short_term_investments" in missing_items(fy2022)
    assert fy2022.cash_and_investments == 150 * M
    # NWC = AR + inventory - AP - deferred revenue
    assert years[2025].net_working_capital == (130 + 65 - 77 - 28) * M


def test_max_years_keeps_most_recent(company_facts):
    years = extract_fiscal_years(company_facts, max_years=2)
    assert [y.fiscal_year for y in years] == [2024, 2025]


def test_sec_client_reads_cache_offline(tmp_path):
    tickers = {"0": {"cik_str": 1234567, "ticker": "EXMPL", "title": "Example Corp"}}
    (tmp_path / "company_tickers.json").write_text(json.dumps(tickers))
    client = SecClient(cache_dir=tmp_path, offline=True)
    assert client.ticker_to_cik("exmpl") == (1234567, "Example Corp")
    with pytest.raises(SecError):
        client.company_facts(1234567)  # not cached and offline -> clear error


def test_user_agent_comes_from_environment(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Jane Doe jane@example.org")
    assert sec_user_agent() == "Jane Doe jane@example.org"
    monkeypatch.delenv("SEC_USER_AGENT")
    assert sec_user_agent() is None

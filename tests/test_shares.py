"""Share count for value per share: latest cover page + dilution, with fallbacks."""

import pytest

from dcf.financials import diluted_share_count
from dcf.model import company_from_facts, select_share_count

M = 1_000_000
FY = {"start": "2025-01-01", "end": "2025-12-31", "unit": "shares"}


def test_diluted_share_count_adds_dilution_gap():
    assert diluted_share_count(95.0, diluted_wa=99.0, basic_wa=97.0) == 97.0
    assert diluted_share_count(95.0, diluted_wa=97.0, basic_wa=99.0) == 95.0  # floored


def test_cover_page_plus_dilution_is_preferred(company_facts, add_fact):
    add_fact(company_facts, "WeightedAverageNumberOfSharesOutstandingBasic", val=97 * M, **FY)
    add_fact(
        company_facts,
        "EntityCommonStockSharesOutstanding",
        "2026-02-05",
        95 * M,
        unit="shares",
        taxonomy="dei",
    )
    company = company_from_facts("EXMPL", company_facts)
    shares, source = select_share_count(company, override=None)
    # 95m outstanding after buybacks + (99m diluted - 97m basic) = 97m, not the 99m average
    assert shares == pytest.approx(97 * M)
    assert "cover page" in source


def test_falls_back_to_weighted_average_without_cover_page(company_facts):
    company = company_from_facts("EXMPL", company_facts)
    shares, source = select_share_count(company, override=None)
    assert shares == 99 * M
    assert "weighted-average" in source


def test_implausible_cover_page_count_is_ignored(company_facts, add_fact):
    # e.g. a dual-class company whose cover page fact covers only one class
    add_fact(company_facts, "WeightedAverageNumberOfSharesOutstandingBasic", val=97 * M, **FY)
    add_fact(
        company_facts,
        "EntityCommonStockSharesOutstanding",
        "2026-02-05",
        40 * M,
        unit="shares",
        taxonomy="dei",
    )
    shares, _ = select_share_count(company_from_facts("EXMPL", company_facts), override=None)
    assert shares == 99 * M


def test_override_wins(company_facts):
    company = company_from_facts("EXMPL", company_facts)
    assert select_share_count(company, override=50e6) == (
        50e6,
        "manual override (market.diluted_shares)",
    )

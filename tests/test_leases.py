"""Finance leases: debt-like in the bridge and WACC, lease-financed assets as capex."""

from pathlib import Path

import pytest

from dcf.assumptions import load_assumptions
from dcf.financials import include_finance_leases
from dcf.market import resolve_quote
from dcf.model import company_from_facts, run_dcf
from dcf.xbrl import extract_fiscal_years

CONFIG = Path(__file__).parents[1] / "config" / "example_offline.yaml"
M = 1_000_000


@pytest.fixture
def leased_facts(company_facts, add_fact):
    """The fixture plus FY2025 lease disclosures (USD m): liability 120, new ROU assets 15,
    ROU amortisation 6, operating lease liability 40."""
    year = {"start": "2025-01-01", "end": "2025-12-31"}
    add_fact(company_facts, "FinanceLeaseLiability", "2025-12-31", 120 * M)
    add_fact(
        company_facts,
        "RightOfUseAssetObtainedInExchangeForFinanceLeaseLiability",
        val=15 * M,
        **year,
    )
    add_fact(company_facts, "FinanceLeaseRightOfUseAssetAmortization", val=6 * M, **year)
    add_fact(company_facts, "OperatingLeaseLiabilityNoncurrent", "2025-12-31", 30 * M)
    add_fact(company_facts, "OperatingLeaseLiabilityCurrent", "2025-12-31", 10 * M)
    return company_facts


def test_lease_items_are_extracted(leased_facts):
    latest = extract_fiscal_years(leased_facts)[-1]
    assert latest.finance_lease_liabilities == 120 * M
    assert latest.finance_lease_additions == 15 * M
    assert latest.finance_lease_amortization == 6 * M
    # Rebuilt from current + noncurrent parts
    assert latest.operating_lease_liabilities == 40 * M


def test_lease_additions_count_as_capex_and_amortization_as_da(leased_facts):
    latest = extract_fiscal_years(leased_facts)[-1]
    adjusted = include_finance_leases(latest)
    assert adjusted.capex == (66 + 15) * M
    # The fixture's D&A comes from note-level Depreciation + intangibles -> add ROU amort.
    assert adjusted.depreciation_amortization == (52 + 6) * M
    assert "RightOfUseAsset" in adjusted.sources["capex"]
    assert include_finance_leases(adjusted) == adjusted  # idempotent


def test_cash_flow_da_total_is_not_topped_up(leased_facts):
    latest = extract_fiscal_years(leased_facts)[-1]
    latest.sources["depreciation_amortization"] = "DepreciationDepletionAndAmortization"
    adjusted = include_finance_leases(latest)
    assert adjusted.depreciation_amortization == latest.depreciation_amortization


def test_bridge_and_wacc_include_finance_leases(leased_facts):
    company = company_from_facts("EXMPL", leased_facts)
    assumptions = load_assumptions(CONFIG)
    quote = resolve_quote("EXMPL", None, assumptions.market.price, assumptions.market.beta)
    result = run_dcf(company, quote, assumptions, with_sensitivity=False)
    g = result.gordon
    assert result.bridge.lease_liabilities == 120 * M
    # Operating leases are NOT subtracted: their rent is already inside EBIT.
    assert g.equity_value == pytest.approx(g.enterprise_value - 260 * M - 120 * M + 280 * M)
    assert result.wacc_inputs.debt == pytest.approx(380 * M)
    # Historical capex used for the projection defaults now includes the leased assets.
    assert result.company.latest.capex == 81 * M


def test_toggle_off_ignores_finance_leases(leased_facts):
    company = company_from_facts("EXMPL", leased_facts)
    assumptions = load_assumptions(CONFIG, {"balance_sheet": {"finance_leases_as_debt": False}})
    quote = resolve_quote("EXMPL", None, assumptions.market.price, assumptions.market.beta)
    result = run_dcf(company, quote, assumptions, with_sensitivity=False)
    assert result.bridge.lease_liabilities == 0.0
    assert result.company.latest.capex == 66 * M


def test_lease_override_from_yaml(company_facts):
    company = company_from_facts("EXMPL", company_facts)
    assumptions = load_assumptions(CONFIG, {"balance_sheet": {"finance_lease_liabilities": 5e7}})
    quote = resolve_quote("EXMPL", None, assumptions.market.price, assumptions.market.beta)
    result = run_dcf(company, quote, assumptions, with_sensitivity=False)
    assert result.bridge.lease_liabilities == 5e7

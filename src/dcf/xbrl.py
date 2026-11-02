"""Turn SEC "companyfacts" JSON into clean annual (10-K) line items.

Background for non-programmers:
- Every number in a 10-K is tagged with an XBRL "concept" from the US-GAAP taxonomy,
  e.g. ``OperatingIncomeLoss`` for EBIT.
- Companies do not all use the same tag for the same economic item (and sometimes switch
  tags over time, e.g. revenue moved to ``RevenueFromContractWithCustomer...`` after the
  ASC 606 revenue standard). We therefore keep an ordered list of candidate tags per line
  item and use the first one that has a value for that fiscal year.
- A 10-K also repeats prior-year comparatives. When the same period is reported in
  several filings we keep the most recently filed value, so restatements win.

This module is pure: it only transforms a dict that was already downloaded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

PeriodKind = Literal["duration", "instant"]

# Forms that contain audited annual statements. 10-K/A (amendments) can restate numbers.
ANNUAL_FORMS = {"10-K", "10-K/A"}

# A fiscal year is ~365 days; 52/53-week years (e.g. Apple) give 364 or 371 days.
# Anything outside this window is a quarter, a half-year or a stub period.
MIN_ANNUAL_DAYS = 350
MAX_ANNUAL_DAYS = 380


@dataclass(frozen=True)
class TagRule:
    """How to find one line item in the filings.

    ``candidates`` is an ordered list. Each candidate is a tuple of tags that are summed;
    most candidates are a single tag, but e.g. D&A can be rebuilt as
    Depreciation + Amortization of intangibles if no combined tag exists.
    """

    name: str
    kind: PeriodKind
    candidates: tuple[tuple[str, ...], ...]
    unit: str = "USD"


# Ordered fallbacks per line item. Flow items (income/cash flow statement) are "duration"
# facts covering a full fiscal year; balance sheet items are "instant" facts at year end.
TAG_RULES: tuple[TagRule, ...] = (
    TagRule(
        "revenue",
        "duration",
        (
            ("RevenueFromContractWithCustomerExcludingAssessedTax",),
            ("Revenues",),
            ("SalesRevenueNet",),
            ("RevenueFromContractWithCustomerIncludingAssessedTax",),
        ),
    ),
    # EBIT proxy: reported operating income (before interest and taxes).
    TagRule("ebit", "duration", (("OperatingIncomeLoss",),)),
    # Depreciation & amortization is a non-cash expense inside EBIT; it is added back in FCF.
    TagRule(
        "depreciation_amortization",
        "duration",
        (
            ("DepreciationDepletionAndAmortization",),
            ("DepreciationAndAmortization",),
            ("DepreciationAmortizationAndAccretionNet",),
            ("Depreciation", "AmortizationOfIntangibleAssets"),
            ("Depreciation",),
        ),
    ),
    # Capex is reported as a positive cash outflow in the investing section.
    TagRule(
        "capex",
        "duration",
        (
            ("PaymentsToAcquirePropertyPlantAndEquipment",),
            ("PaymentsToAcquireProductiveAssets",),
        ),
    ),
    TagRule("income_tax_expense", "duration", (("IncomeTaxExpenseBenefit",),)),
    TagRule(
        "pretax_income",
        "duration",
        (
            (
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            ),
            (
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
            ),
        ),
    ),
    # Weighted-average shares over the fiscal year. Diluted minus basic is the number of
    # extra shares from options, RSUs and convertibles (treasury stock method), which is
    # added to the latest share count in the bridge (see model.select_share_count).
    TagRule(
        "diluted_shares",
        "duration",
        (("WeightedAverageNumberOfDilutedSharesOutstanding",),),
        unit="shares",
    ),
    TagRule(
        "basic_shares",
        "duration",
        (("WeightedAverageNumberOfSharesOutstandingBasic",),),
        unit="shares",
    ),
    # Operating working capital components (balance sheet, year end).
    TagRule("receivables", "instant", (("AccountsReceivableNetCurrent",),)),
    TagRule("inventory", "instant", (("InventoryNet",),)),
    TagRule("payables", "instant", (("AccountsPayableCurrent",),)),
    TagRule(
        "deferred_revenue",
        "instant",
        (("ContractWithCustomerLiabilityCurrent",), ("DeferredRevenueCurrent",)),
    ),
    # Items for the equity bridge.
    TagRule(
        "cash",
        "instant",
        (
            ("CashAndCashEquivalentsAtCarryingValue",),
            ("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",),
        ),
    ),
    TagRule(
        "short_term_investments",
        "instant",
        (("ShortTermInvestments",), ("MarketableSecuritiesCurrent",)),
    ),
    # ``LongTermDebt`` includes current maturities; otherwise rebuild it from its parts.
    TagRule(
        "long_term_debt",
        "instant",
        (
            ("LongTermDebt",),
            ("LongTermDebtNoncurrent", "LongTermDebtCurrent"),
            ("LongTermDebtNoncurrent",),
        ),
    ),
    TagRule(
        "short_term_borrowings",
        "instant",
        (("ShortTermBorrowings",), ("CommercialPaper",)),
    ),
    # Leases (ASC 842). A finance lease is economically a debt-financed asset purchase:
    # the liability is debt-like, the new right-of-use (ROU) asset is investment that never
    # shows up in cash capex, and its amortisation is a non-cash charge inside EBIT.
    TagRule(
        "finance_lease_liabilities",
        "instant",
        (
            ("FinanceLeaseLiability",),
            ("FinanceLeaseLiabilityCurrent", "FinanceLeaseLiabilityNoncurrent"),
            ("FinanceLeaseLiabilityNoncurrent",),
        ),
    ),
    TagRule(
        "finance_lease_additions",
        "duration",
        (("RightOfUseAssetObtainedInExchangeForFinanceLeaseLiability",),),
    ),
    TagRule(
        "finance_lease_amortization",
        "duration",
        (("FinanceLeaseRightOfUseAssetAmortization",),),
    ),
    # Operating leases: the rent is an operating expense inside EBIT, so the liability is
    # shown for information only and NOT subtracted in the bridge (that would count the
    # same lease twice: once as rent in FCF, once as debt).
    TagRule(
        "operating_lease_liabilities",
        "instant",
        (
            ("OperatingLeaseLiability",),
            ("OperatingLeaseLiabilityCurrent", "OperatingLeaseLiabilityNoncurrent"),
            ("OperatingLeaseLiabilityNoncurrent",),
        ),
    ),
)

RULES_BY_NAME = {rule.name: rule for rule in TAG_RULES}


@dataclass
class FiscalYearData:
    """Annual line items for one fiscal year (absolute USD; shares in units)."""

    fiscal_year: int
    period_end: str
    revenue: float
    ebit: float | None = None
    depreciation_amortization: float | None = None
    capex: float | None = None
    income_tax_expense: float | None = None
    pretax_income: float | None = None
    diluted_shares: float | None = None
    basic_shares: float | None = None
    receivables: float | None = None
    inventory: float | None = None
    payables: float | None = None
    deferred_revenue: float | None = None
    cash: float | None = None
    short_term_investments: float | None = None
    long_term_debt: float | None = None
    short_term_borrowings: float | None = None
    finance_lease_liabilities: float | None = None
    finance_lease_additions: float | None = None
    finance_lease_amortization: float | None = None
    operating_lease_liabilities: float | None = None
    # Which tag(s) delivered each value, e.g. {"revenue": "Revenues"} - for transparency.
    sources: dict[str, str] = field(default_factory=dict)
    # True once capex / D&A have been adjusted for finance leases (see financials.py).
    includes_finance_leases: bool = False

    @property
    def net_working_capital(self) -> float:
        """Operating NWC = receivables + inventory - payables - deferred revenue.

        Cash and debt are excluded on purpose: they enter the valuation in the equity
        bridge, so counting them here would double count. Missing components count as 0.
        """
        return (
            (self.receivables or 0.0)
            + (self.inventory or 0.0)
            - (self.payables or 0.0)
            - (self.deferred_revenue or 0.0)
        )

    @property
    def total_debt(self) -> float:
        """Book value of financial debt (long-term incl. current portion + short-term)."""
        return (self.long_term_debt or 0.0) + (self.short_term_borrowings or 0.0)

    @property
    def cash_and_investments(self) -> float:
        """Cash plus short-term marketable securities, treated as non-operating assets."""
        return (self.cash or 0.0) + (self.short_term_investments or 0.0)


def _days_between(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def is_annual_fact(fact: dict, kind: PeriodKind) -> bool:
    """True if a single XBRL fact is an annual 10-K value of the right period type."""
    if fact.get("form") not in ANNUAL_FORMS or fact.get("fp") != "FY":
        return False
    if kind == "instant":
        return "start" not in fact
    if "start" not in fact:
        return False
    return MIN_ANNUAL_DAYS <= _days_between(fact["start"], fact["end"]) <= MAX_ANNUAL_DAYS


def annual_values(facts: dict, tag: str, kind: PeriodKind, unit: str) -> dict[str, float]:
    """Map period-end date -> value for one tag, using only annual 10-K facts.

    If several filings report the same period (original + comparatives in later 10-Ks),
    the most recently filed one wins, so restated numbers replace the originals.
    """
    entries = facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get(unit, [])
    best: dict[str, dict] = {}
    for fact in entries:
        if not is_annual_fact(fact, kind):
            continue
        end = fact["end"]
        if end not in best or fact.get("filed", "") > best[end].get("filed", ""):
            best[end] = fact
    return {end: float(fact["val"]) for end, fact in best.items()}


def resolve_rule(facts: dict, rule: TagRule, period_end: str) -> tuple[float | None, str | None]:
    """Value of one line item for one period end, walking the fallback list in order.

    Returns (value, source tag description) or (None, None) if no candidate is available.
    A multi-tag candidate is used only if *all* its tags are present for that year.
    """
    for candidate in rule.candidates:
        parts = [
            annual_values(facts, tag, rule.kind, rule.unit).get(period_end) for tag in candidate
        ]
        if all(part is not None for part in parts):
            return sum(parts), " + ".join(candidate)
    return None, None


def fiscal_year_ends(facts: dict) -> list[str]:
    """All fiscal-year end dates for which annual revenue exists (any revenue candidate)."""
    ends: set[str] = set()
    rule = RULES_BY_NAME["revenue"]
    for candidate in rule.candidates:
        for tag in candidate:
            ends.update(annual_values(facts, tag, rule.kind, rule.unit))
    return sorted(ends)


def extract_fiscal_years(facts: dict, max_years: int = 5) -> list[FiscalYearData]:
    """Build the last ``max_years`` fiscal years of line items, oldest first.

    The fiscal year label is the calendar year of the period end (e.g. Microsoft's year
    ending 2026-06-30 is labelled 2026).
    """
    years: list[FiscalYearData] = []
    for end in fiscal_year_ends(facts)[-max_years:]:
        values: dict[str, float | None] = {}
        sources: dict[str, str] = {}
        for rule in TAG_RULES:
            value, source = resolve_rule(facts, rule, end)
            values[rule.name] = value
            if source is not None:
                sources[rule.name] = source
        revenue = values.pop("revenue")
        if revenue is None:  # cannot happen by construction, but keep the type checker honest
            continue
        years.append(
            FiscalYearData(
                fiscal_year=date.fromisoformat(end).year,
                period_end=end,
                revenue=revenue,
                sources=sources,
                **values,
            )
        )
    return years


# Filings whose cover page states the number of shares outstanding.
COVER_PAGE_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A"}


def latest_shares_outstanding(facts: dict) -> tuple[float, str] | None:
    """Most recent share count from a filing cover page (dei:EntityCommonStockSharesOutstanding).

    The cover page reports shares outstanding a few weeks after the period end, so it is
    closer to the valuation date than the fiscal-year weighted average and already reflects
    recent buybacks. Returns (shares, as-of date) or None if not reported.
    """
    entries = (
        facts.get("facts", {})
        .get("dei", {})
        .get("EntityCommonStockSharesOutstanding", {})
        .get("units", {})
        .get("shares", [])
    )
    cover = [f for f in entries if f.get("form") in COVER_PAGE_FORMS]
    if not cover:
        return None
    latest = max(cover, key=lambda f: (f["end"], f.get("filed", "")))
    return float(latest["val"]), latest["end"]


def missing_items(year: FiscalYearData) -> list[str]:
    """Names of line items that could not be found for a fiscal year (for warnings)."""
    return [rule.name for rule in TAG_RULES if rule.name not in year.sources]


# (first fiscal year, last fiscal year, tag or None if the item is missing)
SourceRun = tuple[int, int, str | None]


def source_runs(years: list[FiscalYearData], item: str) -> list[SourceRun]:
    """Consecutive fiscal years that took a line item from the same tag (or none).

    The fallback tags are applied year by year, so one series can mix definitions, e.g.
    revenue from ``Revenues`` in one year and from the ASC 606 tag in the next. More than
    one run means the source changed somewhere in the history.
    """
    runs: list[SourceRun] = []
    for year in years:
        tag = year.sources.get(item)
        if runs and runs[-1][2] == tag:
            runs[-1] = (runs[-1][0], year.fiscal_year, tag)
        else:
            runs.append((year.fiscal_year, year.fiscal_year, tag))
    return runs


def changed_sources(years: list[FiscalYearData]) -> dict[str, list[SourceRun]]:
    """Line items whose XBRL source differs between the given years, in name order."""
    items = sorted(set().union(*(year.sources for year in years))) if years else []
    runs = {item: source_runs(years, item) for item in items}
    return {item: r for item, r in runs.items() if len(r) > 1}

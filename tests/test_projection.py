import pytest

from dcf.financials import cagr, summarize
from dcf.projection import (
    Scenario,
    ScenarioSpec,
    fade_path,
    glide_path,
    project,
    resolve_scenario,
    with_constant_growth,
)
from dcf.xbrl import extract_fiscal_years


def simple_scenario(years: int = 5) -> Scenario:
    return Scenario(
        name="test",
        revenue_growth=(0.10,) * years,
        ebit_margin=(0.20,) * years,
        da_pct_revenue=(0.05,) * years,
        capex_pct_revenue=(0.06,) * years,
        nwc_pct_revenue=0.10,
        tax_rate=0.25,
        terminal_growth=0.02,
        exit_multiple=10.0,
    )


def test_fade_path_ends_at_terminal_growth():
    path = fade_path(0.10, 0.02, 5)
    assert path[0] == pytest.approx(0.10)
    assert path[-1] == pytest.approx(0.02)
    assert path[2] == pytest.approx(0.06)  # linear midpoint


def test_glide_path_reaches_target_in_final_year():
    path = glide_path(0.20, 0.30, 5)
    assert path == pytest.approx((0.22, 0.24, 0.26, 0.28, 0.30))


def test_ufcf_year_one_by_hand():
    row = project(simple_scenario(), base_revenue=1_000.0, base_nwc=100.0, base_fiscal_year=2025)[0]
    # Revenue 1,100; EBIT 220; NOPAT 165; D&A 55; capex 66; NWC 110 -> dNWC 10
    assert row.fiscal_year == 2026
    assert row.revenue == pytest.approx(1_100.0)
    assert row.nopat == pytest.approx(165.0)
    assert row.delta_nwc == pytest.approx(10.0)
    assert row.ufcf == pytest.approx(165.0 + 55.0 - 66.0 - 10.0)
    assert row.ebitda == pytest.approx(275.0)


def test_constant_growth_override():
    scenario = with_constant_growth(simple_scenario(), 0.03)
    assert scenario.revenue_growth == (0.03,) * 5


def test_cagr():
    assert cagr(100.0, 121.0, 2) == pytest.approx(0.10)
    assert cagr(0.0, 121.0, 2) is None


def test_resolve_scenario_uses_history_for_missing_inputs(company_facts):
    history = summarize(extract_fiscal_years(company_facts))
    # Last three fiscal years of the fixture: revenue 900 -> 1,100 over two years.
    assert history.revenue_cagr == pytest.approx((1100 / 900) ** 0.5 - 1)
    assert history.last_ebit_margin == pytest.approx(0.23)
    spec = ScenarioSpec(name="base", terminal_growth=0.02, ebit_margin_target=0.25)
    scenario = resolve_scenario(spec, history, years=5, tax_rate=0.21)
    assert scenario.revenue_growth[0] == pytest.approx(history.revenue_cagr)
    assert scenario.revenue_growth[-1] == pytest.approx(0.02)
    assert scenario.ebit_margin[-1] == pytest.approx(0.25)
    assert scenario.capex_pct_revenue[0] == pytest.approx(history.avg_capex_pct_revenue)


def test_scenario_shifts_move_growth_and_margin(company_facts):
    history = summarize(extract_fiscal_years(company_facts))
    spec = ScenarioSpec(
        name="bull",
        revenue_growth_start=0.08,
        revenue_growth_shift=0.03,
        ebit_margin_target=0.25,
        ebit_margin_shift=0.02,
    )
    scenario = resolve_scenario(spec, history, years=5, tax_rate=0.21)
    assert scenario.revenue_growth[0] == pytest.approx(0.11)
    assert scenario.ebit_margin[-1] == pytest.approx(0.27)


def test_horizon_must_be_five_to_ten_years(company_facts):
    history = summarize(extract_fiscal_years(company_facts))
    with pytest.raises(ValueError):
        resolve_scenario(ScenarioSpec(name="x"), history, years=4, tax_rate=0.21)

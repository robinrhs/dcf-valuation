"""CLI smoke tests on the offline fixture (no network)."""

import json
from pathlib import Path

from dcf.cli import main

ROOT = Path(__file__).parents[1]
FIXTURE = str(ROOT / "tests" / "fixtures" / "companyfacts_example.json")
CONFIG = str(ROOT / "config" / "example_offline.yaml")
OFFLINE = ["--fixture", FIXTURE, "--config", CONFIG, "--offline"]


def test_value_prints_report(capsys):
    assert main(["value", "EXMPL", *OFFLINE]) == 0
    out = capsys.readouterr().out
    assert "TERMINAL VALUE AND EQUITY BRIDGE" in out
    assert "REVERSE DCF" in out
    assert "not investment advice" in out


def test_all_scenarios_summary(capsys):
    assert main(["value", "EXMPL", *OFFLINE, "--scenario", "all"]) == 0
    out = capsys.readouterr().out
    assert "SCENARIO SUMMARY" in out
    for name in ("bull", "base", "bear"):
        assert f"scenario: {name}" in out


def test_export_writes_files(tmp_path, capsys):
    assert main(["value", "EXMPL", *OFFLINE, "--export", str(tmp_path)]) == 0
    summary = json.loads((tmp_path / "EXMPL_base_summary.json").read_text())
    assert summary["ticker"] == "EXMPL"
    assert (tmp_path / "EXMPL_base_projection.csv").exists()
    assert (tmp_path / "EXMPL_base_sensitivity.csv").exists()


def test_end_year_flag_and_horizon(capsys):
    assert main(["value", "EXMPL", *OFFLINE, "--no-mid-year", "--years", "7"]) == 0
    out = capsys.readouterr().out
    assert "end-of-year discounting, 7-year explicit forecast" in out


def test_historicals_command(capsys):
    assert main(["historicals", "EXMPL", "--fixture", FIXTURE]) == 0
    assert "Depreciation + AmortizationOfIntangibleAssets" in capsys.readouterr().out


def test_offline_without_price_fails_cleanly(capsys):
    assert main(["value", "EXMPL", "--fixture", FIXTURE, "--offline"]) == 1
    assert "market.price" in capsys.readouterr().err


def test_zero_or_negative_price_fails_cleanly(capsys):
    for price in ("0", "-5"):
        assert main(["value", "EXMPL", *OFFLINE, "--price", price]) == 1
        assert "Share price must be a positive number" in capsys.readouterr().err


def test_implausible_beta_fails_cleanly(capsys):
    assert main(["value", "EXMPL", *OFFLINE, "--beta", "11"]) == 1
    assert "plausible range" in capsys.readouterr().err


def test_report_states_valuation_date_and_quote_date(capsys):
    assert main(["value", "EXMPL", *OFFLINE]) == 0
    out = capsys.readouterr().out
    assert "Market data: manual override, date not stated" in out
    assert "Valuation date: FY2025 period end 2025-12-31" in out
    assert "not rolled forward" in out
    assert main(["value", "EXMPL", *OFFLINE, "--as-of", "2026-11-02"]) == 0
    assert "Market data: manual override, as of 2026-11-02" in capsys.readouterr().out

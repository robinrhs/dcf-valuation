"""Command line interface.

Examples:
    python -m dcf value MSFT --scenario base --config config/msft.yaml
    python -m dcf value MSFT --scenario all
    python -m dcf historicals MSFT
    python -m dcf value EXMPL --fixture tests/fixtures/companyfacts_example.json \\
        --config config/example_offline.yaml
"""

from __future__ import annotations

import argparse
import sys

from dcf.assumptions import Assumptions, load_assumptions
from dcf.market import MarketDataError, YFinanceProvider, resolve_quote
from dcf.model import CompanyData, company_from_facts, prepare_company, run_dcf
from dcf.report import DISCLAIMER, export, historicals_table, render, render_scenario_summary
from dcf.sec_client import SecClient, SecError, load_company_facts_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dcf", description="DCF valuation from SEC EDGAR data")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_data_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("ticker", help="US ticker, e.g. MSFT")
        p.add_argument("--fixture", help="read companyfacts JSON from this file (no SEC call)")
        p.add_argument(
            "--offline", action="store_true", help="no network: use cache/fixture + YAML price"
        )
        p.add_argument("--refresh", action="store_true", help="ignore the local SEC cache")
        p.add_argument("--config", help="YAML file with assumption overrides")

    value = sub.add_parser("value", help="run a DCF valuation")
    add_data_args(value)
    value.add_argument("--scenario", default="base", help="scenario name from YAML, or 'all'")
    value.add_argument("--price", type=float, help="override share price")
    value.add_argument("--beta", type=float, help="override beta")
    value.add_argument("--years", type=int, help="explicit forecast years (5-10)")
    value.add_argument(
        "--mid-year",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="mid-year discounting convention (default from YAML)",
    )
    value.add_argument("--export", metavar="DIR", help="write CSV/JSON results to DIR")

    hist = sub.add_parser("historicals", help="show the extracted 10-K line items")
    add_data_args(hist)
    return parser


def cli_overrides(args: argparse.Namespace) -> dict:
    """Translate CLI flags into the same nested structure as the YAML file."""
    overrides: dict = {}
    if getattr(args, "price", None) is not None:
        overrides.setdefault("market", {})["price"] = args.price
    if getattr(args, "beta", None) is not None:
        overrides.setdefault("market", {})["beta"] = args.beta
    if getattr(args, "years", None) is not None:
        overrides.setdefault("projection", {})["years"] = args.years
    if getattr(args, "mid_year", None) is not None:
        overrides.setdefault("projection", {})["mid_year_convention"] = args.mid_year
    return overrides


def load_company(args: argparse.Namespace) -> CompanyData:
    if args.fixture:
        return company_from_facts(args.ticker, load_company_facts_file(args.fixture))
    client = SecClient(offline=args.offline, refresh=args.refresh)
    cik, _ = client.ticker_to_cik(args.ticker)
    return company_from_facts(args.ticker, client.company_facts(cik))


def cmd_value(args: argparse.Namespace, assumptions: Assumptions) -> int:
    company = load_company(args)
    provider = None if args.offline else YFinanceProvider()
    quote = resolve_quote(
        company.ticker, provider, assumptions.market.price, assumptions.market.beta
    )
    names = list(assumptions.scenarios) if args.scenario == "all" else [args.scenario]
    results = [run_dcf(company, quote, assumptions, name) for name in names]
    for result in results:
        print(render(result))
        print()
        if args.export:
            for path in export(result, args.export):
                print(f"wrote {path}")
    if len(results) > 1:
        print(render_scenario_summary(results))
        print(DISCLAIMER)
    return 0


def cmd_historicals(args: argparse.Namespace, assumptions: Assumptions) -> int:
    company = prepare_company(load_company(args), assumptions)
    print(f"{company.name} ({company.ticker}), CIK {company.cik}")
    print(historicals_table(company.fiscal_years, assumptions.tax_rate))
    print("\nXBRL tags used per line item (latest fiscal year):")
    for name, tag in sorted(company.latest.sources.items()):
        print(f"  {name:<28} {tag}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        assumptions = load_assumptions(args.config, cli_overrides(args))
        if args.command == "value":
            return cmd_value(args, assumptions)
        return cmd_historicals(args, assumptions)
    except (SecError, MarketDataError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

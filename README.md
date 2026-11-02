# dcf-valuation

Discounted cash flow (DCF) model for US public companies, built on official SEC EDGAR data.

> **Status: work in progress.** This `main` branch is the project skeleton. The model is
> being integrated through a series of pull requests, listed below. Nothing on this branch
> values a company yet.

## What is on `main` today

- Python package skeleton: `src/dcf`, uv project, Python >= 3.11, locked dependencies in `uv.lock`
- MIT license
- GitHub Actions CI: ruff lint, ruff format check and pytest on Python 3.11 and 3.12
- One smoke test that checks that the package imports

## What is coming (planned integration order)

1. SEC EDGAR client and XBRL extraction of annual 10-K figures
2. Core DCF maths: WACC, unlevered free cash flow projection, terminal value, equity bridge,
   sensitivity grid and reverse DCF
3. YAML assumptions and scenarios, market data provider, valuation pipeline and the
   `python -m dcf` command-line interface
4. Microsoft case study notebook and the full README (method, formulas, example output,
   limitations)
5. Finance leases treated as debt-financed capex; latest cover-page share count plus dilution
6. Follow-ups from a code audit: corrected warnings, input validation, valuation date and data
   provenance in the report

This list is a plan, not a feature list. Each item becomes part of `main` only when its pull
request is merged.

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

## Disclaimer

Educational project. Nothing here is investment advice.

## License

MIT, see [LICENSE](LICENSE).

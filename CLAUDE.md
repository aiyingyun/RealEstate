# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

A three-stage pipeline for analyzing **Atlanta assumable mortgage listings** from Withroam.com:

1. **Scrape** — crawl Withroam.com for listings with assumable mortgages
2. **Enrich** — fetch Zillow rent estimates and price history per property
3. **Dashboard** — Dash web app with interactive filters and cash flow modeling

## Commands

### Run the full pipeline
```bash
# Recommended: run stages separately
python scripts/script_scrape_withroam.py --zip 30324        # single zip for testing
python scripts/script_scrape_withroam.py                    # all ~40 Atlanta zips (slow)

export REALTYAPI_KEY=your_key
python scripts/script_enrich_zillow.py --input data/listings_enriched.csv

python dashboard/app.py                              # opens on http://localhost:8050
```

### Convenience wrapper (runs all three stages)
```bash
python run.py --zip 30327          # single zip
python run.py --skip-zillow        # skip Zillow enrichment
```

### Zip-level rental comps (optional)
```bash
python scripts/script_search_zip_rentals.py --zip 30324 30327
python scripts/script_search_zip_rentals.py              # all Atlanta zips → data/zip_rental_comps.csv
```

### Tests
```bash
python -m pytest tests/
python -m pytest tests/test_zillow_scraper.py::test_build_zillow_listing_url_removes_duplicate_zip
```

## Architecture

### Data flow
```
Withroam.com → scraper/withroam_scraper.py → data/listings_enriched.csv
                                                        ↓
Zillow API  → scraper/zillow_scraper.py   → (appends rent columns)
                                          → data/zillow_history.csv
                                                        ↓
                                          dashboard/app.py (reads CSVs at startup)
```

### Key modules

**`scraper/withroam_scraper.py`** — Scrapes listing pages. The page structure is label/value on consecutive lines (e.g. "Property tax" / "$134"), so parsing uses line-by-line text extraction rather than CSS selectors. `scrape_atlanta()` is the main entry point.

**`scraper/zillow_scraper.py`** — Rent estimation via RealtyAPI (`GET zillow.realtyapi.io/pro/byaddress`). Returns `rentZestimate`, `zestimate`, and `priceHistory` in one call. Falls back to hardcoded `FALLBACK_RENTS` zip medians when the key is absent or the call fails. Requires `REALTYAPI_KEY` env var.

**`analysis/cashflow.py`** — Pure calculation layer. `calculate_cashflow(row)` takes a property dict and returns full income/expense/return breakdown. `analyze_portfolio(df)` applies it to a DataFrame. Default assumptions (vacancy 5%, maintenance 1%, CapEx $100/mo) live in `DEFAULTS` and are overridable.

**`dashboard/app.py`** — Dash app. Loads CSVs once at startup into `RAW_DF`. All filter callbacks re-run `analyze_portfolio()` with the sidebar's current assumption sliders, so cash flow updates live. Property detail panel shows per-property breakdown + Zillow rent/sale history charts.

**`project_paths.py`** — Single source of truth for all file paths (`LISTINGS_ENRICHED_PATH`, `ZILLOW_HISTORY_PATH`, etc.).

### Data files
- `data/listings_enriched.csv` — primary output, written by both scrape and enrich stages
- `data/zillow_history.csv` — per-property rent and sale price history points from Zillow

### Environment variables
- `REALTYAPI_KEY` — RealtyAPI key for `zillow.realtyapi.io`. Without it, enrichment uses hardcoded zip medians only.

## Notes

- The dashboard has bilingual labels (Chinese/English) in `COL_RENAME` and throughout the UI — this is intentional.
- `withroam_scraper.py` has a hardcoded `ATLANTA_ZIPS` list (~40 zips). Use `--zip` or `--max-zips` during development to avoid long scrapes.
- `scripts/` are the canonical entry points; `run.py` is a legacy convenience wrapper.
- **Zip-level rental search (`search_zip_rentals.py`) always uses `FALLBACK_RENTS`** — RealtyAPI has no zip-level search endpoint, and Zillow direct scraping is blocked by PerimeterX. Real rent data comes from per-property `/pro/byaddress` calls via `script_enrich_zillow.py`.
"""
Add new zip codes to existing listings data without re-scraping everything.

Option 1 — edit ZIP_CODES below and just run:
    python script_add_zips.py

Option 2 — pass zips on the command line:
    python script_add_zips.py 30033 30030
    python script_add_zips.py 30033 --dry-run
    python script_add_zips.py 30327 --force      # re-scrape even if zip already exists

After adding zips, re-run enrichment to update the RealtyAPI data:
    python scripts/script_enrich_realtyapi.py
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from scraper.withroam_scraper import scrape_search_results
from project_paths import LISTINGS_ENRICHED_PATH
from scripts.script_enrich_realtyapi import (
    DEFAULT_REALTYAPI_KEY,
    enrich_listings as enrich_by_address,
)
from scripts.script_enrich_realtyapi_graphs import enrich_graphs

# ─── Edit this list and run `python script_add_zips.py` ──────────────────────────────
ZIP_CODES = [
    "30075",
    # "30030",
    # add more here...
]
# ──────────────────────────────────────────────────────────────────────────────

FORCE   = False  # set True to re-scrape zips already in the CSV
DRY_RUN = False # set True to preview without saving


def load_existing() -> pd.DataFrame:
    if os.path.exists(LISTINGS_ENRICHED_PATH):
        df = pd.read_csv(LISTINGS_ENRICHED_PATH)
        print(f"Loaded {len(df)} existing listings from {LISTINGS_ENRICHED_PATH}")
        return df
    print("No existing data found — starting fresh.")
    return pd.DataFrame()


def already_scraped_zips(df: pd.DataFrame) -> set[str]:
    if df.empty or "zip" not in df.columns:
        return set()
    return set(df["zip"].dropna().astype(str).unique())


def run(zips: list[str], force: bool, dry_run: bool):
    api_key = os.getenv("REALTYAPI_KEY") or DEFAULT_REALTYAPI_KEY
    existing_df = load_existing()
    done_zips = already_scraped_zips(existing_df)

    new_zips = []
    skipped_zips = []
    for z in zips:
        z = z.strip()
        if z in done_zips and not force:
            skipped_zips.append(z)
        else:
            new_zips.append(z)

    if skipped_zips:
        print(f"\nSkipping already-scraped zips: {', '.join(skipped_zips)}")
        print("  (use --force to re-scrape)")

    if not new_zips:
        print("\nNothing new to scrape.")
        return

    print(f"\nWill scrape {len(new_zips)} new zip(s): {', '.join(new_zips)}")

    if dry_run:
        print("Dry run — exiting without scraping.")
        return

    # Scrape each zip
    all_new = []
    for i, z in enumerate(new_zips, 1):
        print(f"\n[{i}/{len(new_zips)}] Scraping zip: {z}")
        results = scrape_search_results(z)
        print(f"  Found {len(results)} listings")
        for r in results:
            if not r.get("zip"):
                r["zip"] = z
        all_new.extend(results)

    if not all_new:
        print("\nNo new listings found.")
        return

    new_df = pd.DataFrame(all_new)
    if "url" not in new_df.columns:
        print("\nScraped data is missing 'url'; cannot dedupe safely.")
        return

    existing_urls = set()
    if not existing_df.empty and "url" in existing_df.columns:
        existing_urls = set(existing_df["url"].dropna().astype(str))

    incremental_df = new_df[~new_df["url"].astype(str).isin(existing_urls)].copy()
    skipped_existing = len(new_df) - len(incremental_df)
    if skipped_existing:
        print(f"\nSkipped {skipped_existing} already-known listing(s) by URL.")

    if incremental_df.empty:
        print("\nNo new listings to append after dedupe.")
        return

    # Append + deduplicate on URL
    if not existing_df.empty:
        combined = pd.concat([existing_df, incremental_df], ignore_index=True)
    else:
        combined = incremental_df

    # Fill equity_needed if missing
    if "equity_needed" not in combined.columns:
        combined["equity_needed"] = None
    if "loan_balance" in combined.columns and "price" in combined.columns:
        mask = (
            combined["equity_needed"].isna()
            & combined["price"].notna()
            & combined["loan_balance"].notna()
        )
        combined.loc[mask, "equity_needed"] = (
            combined.loc[mask, "price"] - combined.loc[mask, "loan_balance"]
        )

    combined.to_csv(LISTINGS_ENRICHED_PATH, index=False)
    print(f"\nSaved {len(combined)} total listings to {LISTINGS_ENRICHED_PATH}")
    print(f"  (+{len(incremental_df)} new from: {', '.join(new_zips)})")

    print("\nRunning incremental RealtyAPI by-address enrichment for new listings...")
    by_address_stats = enrich_by_address(incremental_df, api_key=api_key, sleep_seconds=0.2)
    print(
        f"  By-address: fetched {by_address_stats['fetched']} "
        f"(cached {by_address_stats['cached']}, errors {by_address_stats['errors']})"
    )

    print("\nRunning incremental RealtyAPI graph enrichment for new listings...")
    graph_input_df = incremental_df.copy()
    if os.path.exists(LISTINGS_ENRICHED_PATH):
        enriched_df = pd.read_csv(LISTINGS_ENRICHED_PATH)
        if "url" in enriched_df.columns:
            graph_input_df = enriched_df[enriched_df["url"].astype(str).isin(incremental_df["url"].astype(str))].copy()
    graph_stats = enrich_graphs(graph_input_df, api_key=api_key, sleep_seconds=0.2)
    print(
        f"  Graphs: fetched {graph_stats['fetched']} "
        f"(cached {graph_stats['cached']}, errors {graph_stats['errors']})"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Scrape new zip codes and append to listings_enriched.csv"
    )
    parser.add_argument("zips", nargs="*", help="Zip codes to add (overrides ZIP_CODES list)")
    parser.add_argument("--force",   action="store_true", help="Re-scrape zips already in the CSV")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't save")
    args = parser.parse_args()

    zips    = args.zips if args.zips else ZIP_CODES
    force   = args.force   or FORCE
    dry_run = args.dry_run or DRY_RUN

    if not zips:
        print("No zip codes specified. Edit ZIP_CODES in script_add_zips.py or pass them as arguments.")
        return

    run(zips, force, dry_run)


if __name__ == "__main__":
    main()

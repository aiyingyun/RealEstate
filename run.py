"""
Master runner: scrape withroam + enrich with Zillow rent + save enriched CSV.
Usage:
    python run.py                    # scrape all Atlanta zips (slow, ~40 zips)
    python run.py --zip 30327        # single zip for testing
    python run.py --zip 30327 30305  # multiple zips
    python run.py --skip-zillow      # skip Zillow enrichment (faster)
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scraper.withroam_scraper import scrape_atlanta, ATLANTA_ZIPS
from scraper.zillow_scraper import enrich_with_rent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", nargs="+", help="Zip codes to scrape")
    parser.add_argument("--skip-zillow", action="store_true", help="Skip Zillow enrichment")
    parser.add_argument("--max-zips", type=int, help="Limit number of zip codes")
    args = parser.parse_args()

    zip_codes = args.zip if args.zip else None

    # Step 1: Scrape Withroam
    print("=" * 60)
    print("STEP 1: Scraping Withroam.com")
    print("=" * 60)
    df = scrape_atlanta(zip_codes=zip_codes, max_zips=args.max_zips)

    if df.empty:
        print("No data scraped. Exiting.")
        sys.exit(1)

    raw_path = "data/listings_raw.csv"
    df.to_csv(raw_path, index=False)
    print(f"\nRaw data saved: {raw_path} ({len(df)} listings)")

    # Step 2: Enrich with Zillow rents
    if not args.skip_zillow:
        print("\n" + "=" * 60)
        print("STEP 2: Getting Zillow Rent Estimates")
        print("=" * 60)
        df = enrich_with_rent(df)

    enriched_path = "data/listings_enriched.csv"
    df.to_csv(enriched_path, index=False)
    print(f"\nEnriched data saved: {enriched_path} ({len(df)} listings)")

    # Step 3: Quick cash flow preview
    print("\n" + "=" * 60)
    print("STEP 3: Cash Flow Preview (top 10 by monthly CF)")
    print("=" * 60)

    from analysis.cashflow import analyze_portfolio
    analyzed = analyze_portfolio(df)

    preview_cols = ["address", "zip", "price", "equity_needed", "assumable_rate_pct",
                    "gross_rent", "monthly_cashflow", "cash_on_cash_pct"]
    preview_cols = [c for c in preview_cols if c in analyzed.columns]

    top10 = analyzed.sort_values("monthly_cashflow", ascending=False).head(10)
    print(top10[preview_cols].to_string(index=False))

    print("\nDone! Launch dashboard with:")
    print("  streamlit run dashboard/app.py")


if __name__ == "__main__":
    main()

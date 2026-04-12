"""
Scrape Withroam listings and save them to the project data directory.

Usage:
    python scripts/script_scrape_withroam.py
    python scripts/script_scrape_withroam.py --zip 30324
    python scripts/script_scrape_withroam.py --zip 30324 30305
    python scripts/script_scrape_withroam.py --max-zips 3
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.withroam_scraper import scrape_atlanta
from project_paths import LISTINGS_ENRICHED_PATH


def main():
    parser = argparse.ArgumentParser(description="Scrape listings from Withroam")
    parser.add_argument("--zip", nargs="+", help="Zip codes to scrape")
    parser.add_argument("--max-zips", type=int, help="Limit number of zip codes")
    parser.add_argument(
        "--output",
        default=LISTINGS_ENRICHED_PATH,
        help="CSV path for scraped listings",
    )
    args = parser.parse_args()

    df = scrape_atlanta(zip_codes=args.zip, max_zips=args.max_zips)
    if df.empty:
        print("No data scraped. Exiting.")
        raise SystemExit(1)

    df.to_csv(args.output, index=False)
    print(f"Saved scraped listings: {args.output} ({len(df)} listings)")
    print("Next step:")
    print(f"  python scripts/enrich_zillow.py --input {args.output}")


if __name__ == "__main__":
    main()

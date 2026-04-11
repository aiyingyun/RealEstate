"""
Withroam.com scraper for Atlanta assumable mortgage listings.
Scrapes property data including price, loan details, and operating costs.
"""

import requests
from bs4 import BeautifulSoup
import json
import pandas as pd
import time
import re
from urllib.parse import urljoin

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Atlanta zip codes to scrape
ATLANTA_ZIPS = [
    "30301", "30302", "30303", "30304", "30305",
    "30306", "30307", "30308", "30309", "30310",
    "30311", "30312", "30313", "30314", "30315",
    "30316", "30317", "30318", "30319", "30324",
    "30326", "30327", "30328", "30329", "30331",
    "30332", "30334", "30336", "30338", "30339",
    "30340", "30341", "30342", "30344", "30345",
    "30346", "30349", "30350", "30354", "30360",
]


def parse_price(text: str) -> float | None:
    """Convert price string like '$455,000' to float."""
    if not text:
        return None
    cleaned = re.sub(r"[^0-9.]", "", text.replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


def scrape_listing_page(url: str) -> dict | None:
    """Scrape a single property listing page for detailed data."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  Error fetching {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    data = {"url": url}

    # --- Try JSON-LD structured data first ---
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            obj = json.loads(script.string or "")
            if isinstance(obj, list):
                obj = obj[0]
            if obj.get("@type") in ("SingleFamilyResidence", "House", "Residence", "RealEstateListing"):
                data["address"] = obj.get("address", {}).get("streetAddress", "")
                data["city"] = obj.get("address", {}).get("addressLocality", "")
                data["state"] = obj.get("address", {}).get("addressRegion", "")
                data["zip"] = obj.get("address", {}).get("postalCode", "")
                data["price"] = parse_price(str(obj.get("price", "") or obj.get("offers", {}).get("price", "")))
        except (json.JSONDecodeError, AttributeError):
            pass

    # --- Parse key mortgage/price fields from page HTML ---
    text = soup.get_text(separator=" ", strip=True)

    def extract_after(label: str, source: str) -> str | None:
        """Extract dollar value that appears after a label."""
        pattern = rf"{re.escape(label)}\s*\$?([\d,]+(?:\.\d+)?)"
        m = re.search(pattern, source, re.IGNORECASE)
        return m.group(1).replace(",", "") if m else None

    def extract_pct_after(label: str, source: str) -> str | None:
        pattern = rf"{re.escape(label)}\s*([\d.]+)\s*%"
        m = re.search(pattern, source, re.IGNORECASE)
        return m.group(1) if m else None

    # Price / list price
    if not data.get("price"):
        price_tag = soup.find(string=re.compile(r"\$[\d,]+", re.I))
        if price_tag:
            data["price"] = parse_price(str(price_tag))

    # Loan balance
    for label in ["Loan Balance", "Remaining Balance", "Mortgage Balance", "Loan balance"]:
        val = extract_after(label, text)
        if val:
            data["loan_balance"] = float(val)
            break

    # Assumable rate
    for label in ["Assumable Rate", "Interest Rate", "Loan Rate", "Rate"]:
        val = extract_pct_after(label, text)
        if val:
            data["assumable_rate_pct"] = float(val)
            break

    # Monthly payment
    for label in ["Monthly Payment", "Mo. Payment", "Payment/mo", "monthly payment"]:
        val = extract_after(label, text)
        if val:
            data["monthly_payment"] = float(val)
            break

    # Down payment / equity needed
    for label in ["Down Payment", "Equity Needed", "Down payment", "down payment"]:
        val = extract_after(label, text)
        if val:
            data["equity_needed"] = float(val)
            break

    # Beds / baths / sqft
    beds_m = re.search(r"(\d+)\s*(?:bed|BR|bedroom)", text, re.I)
    baths_m = re.search(r"(\d+(?:\.\d)?)\s*(?:bath|BA|bathroom)", text, re.I)
    sqft_m = re.search(r"([\d,]+)\s*(?:sq\.?\s*ft|sqft|square feet)", text, re.I)

    if beds_m:
        data["beds"] = int(beds_m.group(1))
    if baths_m:
        data["baths"] = float(baths_m.group(1))
    if sqft_m:
        data["sqft"] = int(sqft_m.group(1).replace(",", ""))

    # Property tax (monthly)
    for label in ["Property Tax", "Taxes", "Tax/mo"]:
        val = extract_after(label, text)
        if val:
            data["monthly_tax"] = float(val) if float(val) < 5000 else float(val) / 12
            break

    # Insurance (monthly)
    for label in ["Insurance", "Homeowners Insurance", "Insurance/mo"]:
        val = extract_after(label, text)
        if val:
            data["monthly_insurance"] = float(val) if float(val) < 2000 else float(val) / 12
            break

    # HOA (monthly)
    for label in ["HOA", "HOA Fee", "HOA/mo"]:
        val = extract_after(label, text)
        if val:
            data["monthly_hoa"] = float(val)
            break

    # Loan type
    loan_type_m = re.search(r"\b(VA|FHA|Conventional|USDA)\b", text, re.I)
    if loan_type_m:
        data["loan_type"] = loan_type_m.group(1).upper()

    # Remaining term
    term_m = re.search(r"(\d+)\s*(?:years?|yr)\s*(?:remaining|left|term)", text, re.I)
    if term_m:
        data["remaining_years"] = int(term_m.group(1))

    return data


def scrape_search_results(zip_code: str) -> list[dict]:
    """Scrape all pages of search results for a given zip code."""
    listings = []
    base_url = f"https://www.withroam.com/zipcode/{zip_code}"

    for page in range(1, 8):  # max 7 pages
        url = base_url if page == 1 else f"{base_url}?page={page}"
        print(f"  Fetching page {page}: {url}")

        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  Error: {e}")
            break

        soup = BeautifulSoup(resp.text, "lxml")

        # Find listing links
        found_links = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/listing/" in href:
                full = urljoin("https://www.withroam.com", href)
                found_links.add(full)

        # Also try JSON-LD ItemList
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                obj = json.loads(script.string or "")
                if obj.get("@type") == "ItemList":
                    for item in obj.get("itemListElement", []):
                        url_val = item.get("url") or item.get("item", {}).get("url")
                        if url_val:
                            found_links.add(url_val)
            except (json.JSONDecodeError, AttributeError):
                pass

        if not found_links:
            print(f"  No listings found on page {page}, stopping.")
            break

        print(f"  Found {len(found_links)} listings on page {page}")
        for link in found_links:
            print(f"    Scraping: {link}")
            detail = scrape_listing_page(link)
            if detail:
                detail.setdefault("zip", zip_code)
                listings.append(detail)
            time.sleep(0.8)  # polite crawl delay

        time.sleep(1.5)

    return listings


def scrape_atlanta(zip_codes: list[str] = None, max_zips: int = None) -> pd.DataFrame:
    """
    Main entry point. Scrapes withroam for Atlanta listings.

    Args:
        zip_codes: List of zip codes to scrape. Defaults to ATLANTA_ZIPS.
        max_zips: Limit number of zip codes (useful for testing).
    """
    if zip_codes is None:
        zip_codes = ATLANTA_ZIPS
    if max_zips:
        zip_codes = zip_codes[:max_zips]

    all_listings = []
    for i, zip_code in enumerate(zip_codes, 1):
        print(f"\n[{i}/{len(zip_codes)}] Scraping zip code: {zip_code}")
        results = scrape_search_results(zip_code)
        print(f"  Got {len(results)} listings")
        all_listings.extend(results)
        time.sleep(2)

    if not all_listings:
        print("No listings found.")
        return pd.DataFrame()

    df = pd.DataFrame(all_listings)

    # Deduplicate by URL
    df = df.drop_duplicates(subset=["url"])

    # Compute equity_needed if missing
    if "equity_needed" not in df.columns:
        df["equity_needed"] = None
    mask = df["equity_needed"].isna() & df["price"].notna() & df.get("loan_balance", pd.Series(dtype=float)).notna()
    if "loan_balance" in df.columns:
        df.loc[mask, "equity_needed"] = df.loc[mask, "price"] - df.loc[mask, "loan_balance"]

    print(f"\nTotal unique listings: {len(df)}")
    return df


if __name__ == "__main__":
    import sys

    # Quick test: scrape just 1 zip code
    test_zip = sys.argv[1] if len(sys.argv) > 1 else "30327"
    print(f"Test scrape for zip: {test_zip}")
    df = scrape_atlanta(zip_codes=[test_zip])

    if not df.empty:
        output = f"/Users/yingyunai/Desktop/Code/RealEstate/data/listings_test.csv"
        df.to_csv(output, index=False)
        print(f"\nSaved to {output}")
        print(df[["address", "price", "loan_balance", "assumable_rate_pct",
                   "monthly_payment", "equity_needed", "beds", "baths"]].to_string())
    else:
        print("No data scraped.")

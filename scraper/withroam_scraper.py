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
    """Scrape a single property listing page for detailed data.

    The page renders data as consecutive lines:
      Label line
      Value line
    e.g. "Property tax" / "$134", "Down payment" / "$42,557"

    We parse by splitting into clean lines and looking up known labels.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  Error fetching {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    data = {"url": url}

    # --- JSON-LD: grab address and price ---
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            obj = json.loads(script.string or "")
            if isinstance(obj, list):
                obj = obj[0]
            if obj.get("@type") in ("SingleFamilyResidence", "House", "Residence", "RealEstateListing"):
                addr = obj.get("address", {})
                data.setdefault("address", addr.get("streetAddress", ""))
                data.setdefault("city", addr.get("addressLocality", ""))
                data.setdefault("state", addr.get("addressRegion", ""))
                data.setdefault("zip", addr.get("postalCode", ""))
                price_raw = obj.get("price") or obj.get("offers", {}).get("price")
                if price_raw:
                    data.setdefault("price", parse_price(str(price_raw)))
        except (json.JSONDecodeError, AttributeError):
            pass

    # --- Line-by-line parsing (matches actual page structure) ---
    # Page renders: Label on one line, value on the next line.
    lines = [l.strip() for l in soup.get_text(separator="\n").split("\n") if l.strip()]

    def next_dollar(label_idx: int) -> float | None:
        """Return the dollar value on the line immediately after label_idx."""
        for offset in range(1, 4):
            if label_idx + offset >= len(lines):
                break
            candidate = lines[label_idx + offset]
            m = re.match(r"^\$?([\d,]+(?:\.\d+)?)$", candidate.replace(",", ""))
            if m:
                return float(m.group(1).replace(",", ""))
            # "$484" style (with dollar sign)
            m2 = re.match(r"^\$([\d,]+(?:\.\d+)?)", candidate)
            if m2:
                return float(m2.group(1).replace(",", ""))
        return None

    def find_line(label: str) -> int | None:
        """Return index of first line matching label (case-insensitive)."""
        for i, line in enumerate(lines):
            if line.lower() == label.lower():
                return i
        return None

    # Price (first big dollar sign near top)
    if not data.get("price"):
        for line in lines[:10]:
            m = re.match(r"^\$([\d,]+)$", line)
            if m:
                val = float(m.group(1).replace(",", ""))
                if val > 10000:
                    data["price"] = val
                    break

    # "Your payment" block: find the "Your payment" label first, then grab the next "$X/mo at Y%" line.
    # The page also shows "$X/mo at 6.4%" (market rate) ABOVE this section — we must NOT pick that up.
    yp_idx = find_line("Your payment")
    if yp_idx is not None:
        for offset in range(1, 5):
            if yp_idx + offset >= len(lines):
                break
            candidate = lines[yp_idx + offset]
            m = re.match(r"^\$([\d,]+)/mo at ([\d.]+)%$", candidate)
            if m:
                data.setdefault("monthly_payment_total", float(m.group(1).replace(",", "")))
                data.setdefault("assumable_rate_pct", float(m.group(2)))
                break

    # "VA loan:" / "FHA loan:" line: "$81,342 at 4.46%"
    for i, line in enumerate(lines):
        loan_m = re.match(r"^(VA|FHA|USDA|Conventional)\s+loan:$", line, re.I)
        if loan_m:
            data.setdefault("loan_type", loan_m.group(1).upper())
            # Next line should be "$81,342 at 4.46%"
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                bal_m = re.match(r"^\$([\d,]+)\s+at\s+([\d.]+)%$", next_line)
                if bal_m:
                    data.setdefault("loan_balance", float(bal_m.group(1).replace(",", "")))
                    data.setdefault("assumable_rate_pct", float(bal_m.group(2)))
            break

    # Payment details block (after line "Payment details")
    pd_idx = find_line("Payment details")
    if pd_idx is not None:
        # Principal/interest → next dollar value
        pi_idx = find_line("Principal/interest")
        if pi_idx and pi_idx > pd_idx:
            val = next_dollar(pi_idx)
            if val:
                data["monthly_payment"] = val  # P&I only

        # Home price
        hp_idx = find_line("Home price")
        if hp_idx and hp_idx > pd_idx:
            val = next_dollar(hp_idx)
            if val and val > 10000:
                data.setdefault("price", val)

        # Down payment (= equity needed)
        dp_idx = find_line("Down payment")
        if dp_idx and dp_idx > pd_idx:
            val = next_dollar(dp_idx)
            if val:
                data["equity_needed"] = val

        # Total loan balance
        tl_idx = find_line("Total loan")
        if tl_idx and tl_idx > pd_idx:
            val = next_dollar(tl_idx)
            if val:
                data.setdefault("loan_balance", val)
            # Rate is in parens on the same line or next: "(4.46%)"
            tl_line = lines[tl_idx]
            rate_m = re.search(r"\(([\d.]+)%\)", tl_line)
            if not rate_m and tl_idx + 1 < len(lines):
                rate_m = re.search(r"\(([\d.]+)%\)", lines[tl_idx + 1])
            if rate_m:
                data.setdefault("assumable_rate_pct", float(rate_m.group(1)))

        # Term
        term_idx = find_line("Term")
        if term_idx and term_idx > pd_idx:
            if term_idx + 1 < len(lines):
                term_m = re.match(r"^(\d+)\s*yrs?$", lines[term_idx + 1])
                if term_m:
                    data["remaining_years"] = int(term_m.group(1))

        # Property tax (monthly)
        tax_idx = find_line("Property tax")
        if tax_idx and tax_idx > pd_idx:
            val = next_dollar(tax_idx)
            if val:
                data["monthly_tax"] = val

        # Home insurance
        ins_idx = find_line("Home insurance")
        if ins_idx and ins_idx > pd_idx:
            val = next_dollar(ins_idx)
            if val:
                data["monthly_insurance"] = val

        # Loan / mortgage insurance
        loan_ins_idx = find_line("Loan insurance")
        if loan_ins_idx is None:
            loan_ins_idx = find_line("Mortgage insurance")
        if loan_ins_idx and loan_ins_idx > pd_idx:
            val = next_dollar(loan_ins_idx)
            if val is not None:
                data["monthly_loan_insurance"] = val

        # HOA
        hoa_idx = find_line("HOA")
        if hoa_idx and hoa_idx > pd_idx:
            val = next_dollar(hoa_idx)
            if val is not None:
                data["monthly_hoa"] = val

    # Beds / baths / sqft (format: "1 bed", "2 bath", "414 sqft")
    full_text = " ".join(lines)
    beds_m = re.search(r"(\d+)\s+bed\b", full_text, re.I)
    baths_m = re.search(r"(\d+(?:\.\d)?)\s+bath\b", full_text, re.I)
    sqft_m = re.search(r"([\d,]+)\s+sqft\b", full_text, re.I)

    if beds_m:
        data["beds"] = int(beds_m.group(1))
    if baths_m:
        data["baths"] = float(baths_m.group(1))
    if sqft_m:
        data["sqft"] = int(sqft_m.group(1).replace(",", ""))

    # Loan type fallback from text
    if "loan_type" not in data:
        lt_m = re.search(r"\b(VA|FHA|USDA)\b", full_text)
        if lt_m:
            data["loan_type"] = lt_m.group(1).upper()
        elif "conventional" in full_text.lower():
            data["loan_type"] = "CONVENTIONAL"

    # Address fallback from page title
    if not data.get("address"):
        title = soup.find("title")
        if title:
            addr_m = re.search(r"Buy\s+(.+?)\s+with a", title.get_text())
            if addr_m:
                data["address"] = addr_m.group(1).strip()

    # Extract zip / city / state from address if missing
    # Address format: "Street, City, GA, 30324"
    addr_str = data.get("address", "")
    if addr_str and not data.get("zip"):
        zip_m = re.search(r"\b(\d{5})\b", addr_str)
        if zip_m:
            data["zip"] = zip_m.group(1)
    if addr_str and not data.get("city"):
        # e.g. "2106 Pine Heights Dr NE, Atlanta, GA, 30324" → Atlanta
        city_m = re.search(r",\s*([^,]+),\s*[A-Z]{2}\b", addr_str)
        if city_m:
            data["city"] = city_m.group(1).strip()
    if addr_str and not data.get("state"):
        state_m = re.search(r",\s*([A-Z]{2})\s*[,\d]", addr_str)
        if state_m:
            data["state"] = state_m.group(1)

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
                # Use search zip as fallback if scraper didn't find one
                if not detail.get("zip"):
                    detail["zip"] = zip_code
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
        output = "/Users/yingyunai/Desktop/Code/RealEstate/data/listings_test.csv"
        df.to_csv(output, index=False)
        print(f"\nSaved to {output}")
        print(f"Columns found: {list(df.columns)}")
        show_cols = [c for c in ["address", "price", "loan_balance", "assumable_rate_pct",
                                  "monthly_payment", "equity_needed", "beds", "baths", "loan_type",
                                  "monthly_tax", "monthly_insurance", "monthly_loan_insurance",
                                  "monthly_hoa", "remaining_years"]
                     if c in df.columns]
        print(df[show_cols].head(10).to_string())
    else:
        print("No data scraped.")

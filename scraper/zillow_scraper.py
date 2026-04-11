"""
Zillow rent comparable scraper.
Fetches estimated rent for a given address/zip using Zillow's Zestimate endpoint.
Falls back to zip-level median rent if individual estimate is unavailable.
"""

import requests
import re
import json
import time
from urllib.parse import quote

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.zillow.com/",
}

# Fallback median rents by Atlanta zip (manual research data as safety net)
FALLBACK_RENTS = {
    "30305": 2800, "30306": 2400, "30307": 2200, "30308": 2100,
    "30309": 2600, "30312": 2000, "30313": 1900, "30314": 1500,
    "30315": 1600, "30316": 1800, "30317": 2000, "30318": 1900,
    "30319": 2500, "30324": 2300, "30326": 3200, "30327": 3500,
    "30328": 2800, "30329": 2200, "30331": 1500, "30336": 1400,
    "30338": 2500, "30339": 2600, "30340": 1900, "30341": 2000,
    "30342": 2800, "30344": 1500, "30345": 2000, "30346": 2700,
    "30349": 1600, "30350": 2400, "30354": 1500, "30360": 1900,
}


def get_zillow_rent_estimate(address: str, zip_code: str, beds: int = 3) -> dict:
    """
    Attempt to get rent estimate from Zillow for a specific address.
    Returns dict with rent_estimate, rent_source, and zillow_url.
    """
    result = {
        "rent_estimate": None,
        "rent_low": None,
        "rent_high": None,
        "rent_source": "none",
        "zillow_url": None,
    }

    if not address:
        return _fallback_rent(zip_code, beds, result)

    # Build Zillow search URL
    search_query = f"{address} {zip_code}".strip()
    encoded = quote(search_query)
    search_url = f"https://www.zillow.com/search/easy/?searchQueryState={{\"pagination\":{{}},\"mapBounds\":{{\"west\":-85.0,\"east\":-84.0,\"south\":33.5,\"north\":34.2}},\"isMapVisible\":false,\"filterState\":{{\"fr\":{{\"value\":true}},\"fsba\":{{\"value\":false}},\"fsbo\":{{\"value\":false}},\"nc\":{{\"value\":false}},\"cmsn\":{{\"value\":false}},\"auc\":{{\"value\":false}},\"fore\":{{\"value\":false}}}},\"isListVisible\":true}}"

    # Try Zillow property page directly
    address_slug = re.sub(r"[^a-zA-Z0-9]", "-", address.lower()).strip("-")
    zillow_url = f"https://www.zillow.com/homes/{encoded}_rb/"
    result["zillow_url"] = f"https://www.zillow.com/homes/{address_slug}-{zip_code}_rb/"

    try:
        resp = requests.get(zillow_url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            # Look for Zestimate rent data in page
            rent_m = re.search(
                r'"rentZestimate"\s*:\s*(\d+)', resp.text
            )
            if rent_m:
                result["rent_estimate"] = int(rent_m.group(1))
                result["rent_source"] = "zillow_zestimate"
                return result

            # Try finding in __NEXT_DATA__ JSON
            next_data_m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', resp.text, re.S)
            if next_data_m:
                try:
                    nd = json.loads(next_data_m.group(1))
                    rent = _deep_find(nd, "rentZestimate")
                    if rent:
                        result["rent_estimate"] = int(rent)
                        result["rent_source"] = "zillow_zestimate"
                        return result
                except (json.JSONDecodeError, TypeError):
                    pass
    except requests.RequestException:
        pass

    # Fallback: search Zillow rentals by zip
    return _search_zip_rentals(zip_code, beds, result)


def _search_zip_rentals(zip_code: str, beds: int, result: dict) -> dict:
    """Search Zillow rental listings for a zip code and return median rent."""
    url = (
        f"https://www.zillow.com/{zip_code}_rb/rentals/"
        f"?searchQueryState=%7B%22pagination%22%3A%7B%7D%2C"
        f"%22isMapVisible%22%3Afalse%2C%22filterState%22%3A%7B"
        f"%22fr%22%3A%7B%22value%22%3Atrue%7D%2C%22fsba%22%3A%7B%22value%22%3Afalse%7D%2C"
        f"%22fsbo%22%3A%7B%22value%22%3Afalse%7D%7D%2C%22isListVisible%22%3Atrue%7D"
    )

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            prices = re.findall(r'"price"\s*:\s*(\d+)', resp.text)
            prices = [int(p) for p in prices if 500 < int(p) < 15000]
            if prices:
                prices.sort()
                # Use median
                mid = len(prices) // 2
                median_rent = prices[mid]
                # Adjust by beds
                median_rent = _adjust_for_beds(median_rent, beds)
                result["rent_estimate"] = median_rent
                result["rent_low"] = prices[len(prices) // 4]
                result["rent_high"] = prices[3 * len(prices) // 4]
                result["rent_source"] = "zillow_zip_median"
                result["zillow_url"] = url
                return result
    except requests.RequestException:
        pass

    return _fallback_rent(zip_code, beds, result)


def _fallback_rent(zip_code: str, beds: int, result: dict) -> dict:
    """Use hardcoded fallback rent data."""
    base = FALLBACK_RENTS.get(str(zip_code), 2000)
    result["rent_estimate"] = _adjust_for_beds(base, beds)
    result["rent_source"] = "fallback_estimate"
    return result


def _adjust_for_beds(base_rent: float, beds: int) -> float:
    """Adjust base rent (assumes 3BR baseline) for actual bed count."""
    adjustments = {1: 0.65, 2: 0.82, 3: 1.0, 4: 1.15, 5: 1.28}
    factor = adjustments.get(beds, 1.0)
    return round(base_rent * factor)


def _deep_find(obj, key: str):
    """Recursively search nested dict/list for a key."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            result = _deep_find(v, key)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _deep_find(item, key)
            if result is not None:
                return result
    return None


def enrich_with_rent(df):
    """Add rent estimates to a listings DataFrame."""
    import pandas as pd

    rent_data = []
    total = len(df)
    for i, row in df.iterrows():
        print(f"  [{i+1}/{total}] Getting rent for: {row.get('address', 'unknown')}")
        beds = int(row.get("beds", 3) or 3)
        rent_info = get_zillow_rent_estimate(
            address=str(row.get("address", "")),
            zip_code=str(row.get("zip", "")),
            beds=beds,
        )
        rent_data.append(rent_info)
        time.sleep(1.2)  # polite delay

    rent_df = pd.DataFrame(rent_data)
    return pd.concat([df.reset_index(drop=True), rent_df], axis=1)


if __name__ == "__main__":
    # Quick test
    result = get_zillow_rent_estimate(
        address="285 CENTENNIAL OLYMPIC PARK DR NW UNIT 1905",
        zip_code="30313",
        beds=2,
    )
    print(result)

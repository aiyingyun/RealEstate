"""
Enrich listing rows with RealtyAPI's Zillow by-address endpoint.

Usage:
    export REALTYAPI_KEY=your_key
    python scripts/script_enrich_realtyapi.py
    python scripts/script_enrich_realtyapi.py --input data/listings_enriched.csv --limit 10
"""

import argparse
import json
import os
import sys
import time
from typing import Any

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from project_paths import (
    LISTINGS_ENRICHED_PATH,
    REALTYAPI_ENRICHED_PATH,
    REALTYAPI_RESPONSES_PATH,
)


API_URL = "https://zillow.realtyapi.io/byaddress"
DEFAULT_REALTYAPI_KEY = "demo"  # Replace with your actual key or set REALTYAPI_KEY env variable


def flatten_json(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dict/list values into scalar columns."""
    flat: dict[str, Any] = {}

    if isinstance(value, dict):
        for key, nested_value in value.items():
            safe_key = str(key).strip().replace(" ", "_").replace("/", "_")
            safe_key = safe_key.replace("(", "").replace(")", "").replace("-", "_")
            next_prefix = f"{prefix}{safe_key}_" if prefix else f"{safe_key}_"
            flat.update(flatten_json(nested_value, next_prefix))
        return flat

    if isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            flat[prefix[:-1]] = json.dumps(value, ensure_ascii=True)
            return flat
        for index, nested_value in enumerate(value):
            flat.update(flatten_json(nested_value, f"{prefix}{index}_"))
        return flat

    flat[prefix[:-1]] = value
    return flat


def load_existing_results(path: str) -> dict[str, dict[str, Any]]:
    """Load prior raw responses keyed by address to support resume."""
    if not os.path.exists(path):
        return {}

    results: dict[str, dict[str, Any]] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            address = record.get("_input_address")
            if address:
                results[address] = record
    return results


def normalize_input_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare listing rows for enrichment."""
    if "address" not in df.columns:
        raise ValueError("Input dataframe is missing an 'address' column.")

    normalized = df.copy()
    normalized["address"] = normalized["address"].fillna("").astype(str).str.strip()
    normalized = normalized[normalized["address"] != ""].copy()
    if "url" in normalized.columns:
        normalized["url"] = normalized["url"].fillna("").astype(str).str.strip()
    return normalized


def fetch_by_address(session: requests.Session, address: str, api_key: str) -> dict[str, Any]:
    """Fetch one address from RealtyAPI and return a raw record."""
    response = session.get(
        API_URL,
        headers={"x-realtyapi-key": api_key},
        params={"propertyaddress": address},
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected response type for {address}: {type(payload).__name__}")

    return {
        "_input_address": address,
        "_fetched_at": pd.Timestamp.utcnow().isoformat(),
        **payload,
    }


def build_output_frame(df: pd.DataFrame, raw_results: dict[str, dict[str, Any]]) -> pd.DataFrame:
    """Join flattened RealtyAPI data back to the input rows."""
    flattened_rows = []
    for address in df["address"]:
        raw_record = raw_results.get(address, {"_input_address": address})
        flattened = flatten_json(raw_record)
        flattened_rows.append(flattened)

    flat_df = pd.DataFrame(flattened_rows)
    overlap = [column for column in flat_df.columns if column in df.columns]
    if overlap:
        flat_df = flat_df.rename(columns={column: f"realtyapi_{column}" for column in overlap})

    return pd.concat([df.reset_index(drop=True), flat_df.reset_index(drop=True)], axis=1)


def append_jsonl(path: str, records: list[dict[str, Any]]) -> None:
    """Append records to a JSONL file."""
    if not records:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def merge_csv_rows(path: str, rows_df: pd.DataFrame, key_candidates: list[list[str]]) -> None:
    """Merge enriched rows into an existing CSV without dropping unrelated rows."""
    if rows_df.empty:
        return

    if not os.path.exists(path):
        rows_df.to_csv(path, index=False)
        return

    existing_df = pd.read_csv(path)
    key_cols: list[str] = []
    for candidate in key_candidates:
        if all(column in existing_df.columns and column in rows_df.columns for column in candidate):
            key_cols = candidate
            break

    if not key_cols:
        raise ValueError("No shared key columns available to merge enriched RealtyAPI rows safely.")

    existing_df = existing_df.copy()
    rows_df = rows_df.copy()
    for column in rows_df.columns:
        if column not in existing_df.columns and column != "_merge_key":
            existing_df[column] = None
    existing_df["_merge_key"] = existing_df[key_cols].astype(str).agg("||".join, axis=1)
    rows_df["_merge_key"] = rows_df[key_cols].astype(str).agg("||".join, axis=1)

    existing_index = {key: idx for idx, key in existing_df["_merge_key"].items()}
    update_cols = [column for column in rows_df.columns if column not in {"_merge_key", *key_cols}]

    for _, row in rows_df.iterrows():
        merge_key = row["_merge_key"]
        if merge_key in existing_index:
            idx = existing_index[merge_key]
            for column in update_cols:
                existing_df.loc[idx, column] = row[column]
        else:
            new_row = {column: None for column in existing_df.columns}
            for column in rows_df.columns:
                if column == "_merge_key":
                    continue
                new_row[column] = row[column]
            existing_df = pd.concat([existing_df, pd.DataFrame([new_row])], ignore_index=True)

    existing_df = existing_df.drop(columns=["_merge_key"], errors="ignore")
    existing_df.to_csv(path, index=False)


def enrich_listings(
    df: pd.DataFrame,
    api_key: str,
    raw_output_path: str = REALTYAPI_RESPONSES_PATH,
    enriched_output_path: str = REALTYAPI_ENRICHED_PATH,
    sleep_seconds: float = 0.2,
    force_refresh: bool = False,
) -> dict[str, int]:
    """Incrementally enrich listing rows and append only newly fetched results."""
    normalized_df = normalize_input_frame(df)
    if normalized_df.empty:
        return {"input_rows": 0, "cached": 0, "fetched": 0, "success": 0, "errors": 0}

    existing_results = {} if force_refresh else load_existing_results(raw_output_path)
    addresses = normalized_df["address"].tolist()
    pending_addresses = [address for address in addresses if address not in existing_results]

    new_records: list[dict[str, Any]] = []
    session = requests.Session()
    try:
        for index, address in enumerate(pending_addresses, start=1):
            print(f"[{index}/{len(pending_addresses)}] Fetching {address}")
            try:
                record = fetch_by_address(session, address, api_key)
            except Exception as exc:  # noqa: BLE001
                record = {
                    "_input_address": address,
                    "_fetched_at": pd.Timestamp.utcnow().isoformat(),
                    "_error": str(exc),
                }
                print(f"  Failed: {exc}")
            existing_results[address] = record
            new_records.append(record)
            if sleep_seconds and index < len(pending_addresses):
                time.sleep(sleep_seconds)
    finally:
        session.close()

    append_jsonl(raw_output_path, new_records)

    fetched_df = normalized_df[normalized_df["address"].isin([record["_input_address"] for record in new_records])].copy()
    if not fetched_df.empty:
        rows_df = build_output_frame(fetched_df, existing_results)
        merge_csv_rows(enriched_output_path, rows_df, key_candidates=[["url"], ["address"]])

    success_count = sum(1 for record in new_records if "_error" not in record)
    error_count = len(new_records) - success_count
    return {
        "input_rows": len(normalized_df),
        "cached": len(addresses) - len(pending_addresses),
        "fetched": len(new_records),
        "success": success_count,
        "errors": error_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich listing addresses with RealtyAPI by-address data")
    parser.add_argument("--input", default=LISTINGS_ENRICHED_PATH, help="Input CSV path")
    parser.add_argument("--output", default=REALTYAPI_ENRICHED_PATH, help="Output CSV path")
    parser.add_argument(
        "--raw-output",
        default=REALTYAPI_RESPONSES_PATH,
        help="JSONL file for raw RealtyAPI responses",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("REALTYAPI_KEY") or DEFAULT_REALTYAPI_KEY,
        help="RealtyAPI key",
    )
    parser.add_argument("--limit", type=int, help="Only process the first N rows")
    parser.add_argument("--sleep-seconds", type=float, default=0.2, help="Delay between requests")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore cached raw responses and refetch every address",
    )
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("Missing RealtyAPI key. Use --api-key or set REALTYAPI_KEY.")

    df = pd.read_csv(args.input)
    if args.limit:
        df = df.head(args.limit).copy()
    stats = enrich_listings(
        df=df,
        api_key=args.api_key,
        raw_output_path=args.raw_output,
        enriched_output_path=args.output,
        sleep_seconds=args.sleep_seconds,
        force_refresh=args.force_refresh,
    )
    print(f"Input rows: {stats['input_rows']}")
    print(f"Cached responses: {stats['cached']}")
    print(f"Requests fetched: {stats['fetched']}")
    print(f"Saved raw responses: {args.raw_output}")
    print(f"Saved enriched CSV: {args.output}")
    print(f"Successful responses: {stats['success']}")
    print(f"Errored responses: {stats['errors']}")


if __name__ == "__main__":
    main()

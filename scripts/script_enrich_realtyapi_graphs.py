"""
Fetch RealtyAPI graph_charts history for each listing.

Stores:
1. Raw JSONL responses for each listing + history type
2. A normalized long CSV of extracted time-series points

Usage:
    export REALTYAPI_KEY=your_key
    python scripts/script_enrich_realtyapi_graphs.py
    python scripts/script_enrich_realtyapi_graphs.py --input data/listings_enriched.csv --limit 10
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
    REALTYAPI_ENRICHED_PATH,
    REALTYAPI_GRAPH_POINTS_PATH,
    REALTYAPI_GRAPH_RAW_PATH,
    REALTYAPI_GRAPH_SUMMARY_PATH,
)


API_URL = "https://zillow.realtyapi.io/graph_charts"
DEFAULT_WHICH = ("rent_zestimate_history", "zestimate_history")
DEFAULT_REALTYAPI_KEY = "demo_key_1234567890abcdef"  # replace with your actual key or set REALTYAPI_KEY


def flatten_json(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested values into scalar columns."""
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


def extract_scalar_metadata(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten scalar metadata while skipping nested object/list payload bodies."""
    flat: dict[str, Any] = {}

    if isinstance(value, dict):
        for key, nested_value in value.items():
            safe_key = str(key).strip().replace(" ", "_").replace("/", "_")
            safe_key = safe_key.replace("(", "").replace(")", "").replace("-", "_")
            next_prefix = f"{prefix}{safe_key}_" if prefix else f"{safe_key}_"
            flat.update(extract_scalar_metadata(nested_value, next_prefix))
        return flat

    if isinstance(value, list):
        if value and all(not isinstance(item, (dict, list)) for item in value):
            flat[prefix[:-1]] = json.dumps(value, ensure_ascii=True)
        return flat

    flat[prefix[:-1]] = value
    return flat


def load_existing_results(path: str) -> dict[tuple[str, str], dict[str, Any]]:
    """Load cached graph responses keyed by (address, which)."""
    if not os.path.exists(path):
        return {}

    results: dict[tuple[str, str], dict[str, Any]] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            address = record.get("_input_address")
            which = record.get("_which")
            if address and which:
                results[(address, which)] = record
    return results


def normalize_input_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare listing rows for graph enrichment."""
    if "address" not in df.columns:
        raise ValueError("Input dataframe is missing an 'address' column.")

    normalized = df.copy()
    normalized["address"] = normalized["address"].fillna("").astype(str).str.strip()
    normalized = normalized[normalized["address"] != ""].copy()
    return normalized


def coerce_zpid(value: Any) -> str | None:
    """Convert floats/strings like 2094515802.0 to Zillow's expected string id."""
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(".0"):
        text = text[:-2]
    return text


def first_nonempty(*values: Any) -> str | None:
    """Return the first non-empty string-like value."""
    for value in values:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def build_query_params(row: pd.Series, which: str) -> dict[str, str]:
    """Build graph_charts query params from a listing row."""
    params = {
        "recent_first": "True",
        "which": which,
    }

    address = first_nonempty(row.get("address"))
    zpid = coerce_zpid(row.get("PropertyZPID"))
    byurl = first_nonempty(row.get("PropertyZillowURL"), row.get("zillow_url"))

    if zpid:
        params["byzpid"] = zpid
    if byurl:
        params["byurl"] = byurl
    if address:
        params["byaddress"] = address

    return params


def fetch_graph(
    session: requests.Session,
    row: pd.Series,
    which: str,
    api_key: str,
) -> dict[str, Any]:
    """Fetch one graph response for one listing."""
    params = build_query_params(row, which)
    if len(params) == 2:
        raise ValueError(f"No usable lookup params for {row.get('address', '')}")

    response = session.get(
        API_URL,
        headers={"x-realtyapi-key": api_key},
        params=params,
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, (dict, list)):
        raise ValueError(f"Unexpected response type for {row.get('address', '')}: {type(payload).__name__}")

    return {
        "_input_address": first_nonempty(row.get("address")),
        "_input_zpid": coerce_zpid(row.get("PropertyZPID")),
        "_input_url": first_nonempty(row.get("PropertyZillowURL"), row.get("zillow_url")),
        "_which": which,
        "_fetched_at": pd.Timestamp.utcnow().isoformat(),
        "payload": payload,
    }


def looks_like_timeseries_point(value: Any) -> bool:
    """Heuristic to identify list items that represent chart points."""
    if not isinstance(value, dict):
        return False

    keys = {str(key).lower() for key in value.keys()}
    date_keys = {"date", "timestamp", "time", "month", "year", "x"}
    value_keys = {"value", "price", "amount", "zestimate", "rent_zestimate", "rentzestimate", "y"}
    return bool(keys & date_keys) or bool(keys & value_keys)


def extract_points(value: Any, path: str = "payload") -> list[dict[str, Any]]:
    """Recursively extract time-series rows from a nested response."""
    points: list[dict[str, Any]] = []

    if isinstance(value, list):
        if value and all(looks_like_timeseries_point(item) for item in value):
            for index, item in enumerate(value):
                flat_item = flatten_json(item)
                flat_item["_series_path"] = path
                flat_item["_series_index"] = index
                points.append(flat_item)
            return points

        for index, item in enumerate(value):
            points.extend(extract_points(item, f"{path}_{index}"))
        return points

    if isinstance(value, dict):
        for key, nested_value in value.items():
            safe_key = str(key).strip().replace(" ", "_").replace("/", "_")
            safe_key = safe_key.replace("(", "").replace(")", "").replace("-", "_")
            points.extend(extract_points(nested_value, f"{path}_{safe_key}"))

    return points


def build_points_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Convert raw graph responses to a long dataframe of chart points."""
    rows: list[dict[str, Any]] = []
    for record in records:
        if "_error" in record:
            rows.append(
                {
                    "_input_address": record.get("_input_address"),
                    "_input_zpid": record.get("_input_zpid"),
                    "_input_url": record.get("_input_url"),
                    "_which": record.get("_which"),
                    "_fetched_at": record.get("_fetched_at"),
                    "_error": record.get("_error"),
                }
            )
            continue

        payload = record.get("payload")
        points = extract_points(payload)
        if not points:
            rows.append(
                {
                    "_input_address": record.get("_input_address"),
                    "_input_zpid": record.get("_input_zpid"),
                    "_input_url": record.get("_input_url"),
                    "_which": record.get("_which"),
                    "_fetched_at": record.get("_fetched_at"),
                    "_note": "no_timeseries_points_detected",
                }
            )
            continue

        for point in points:
            rows.append(
                {
                    "_input_address": record.get("_input_address"),
                    "_input_zpid": record.get("_input_zpid"),
                    "_input_url": record.get("_input_url"),
                    "_which": record.get("_which"),
                    "_fetched_at": record.get("_fetched_at"),
                    **point,
                }
            )

    return pd.DataFrame(rows)


def build_summary_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Convert raw graph responses to one metadata row per response."""
    rows: list[dict[str, Any]] = []
    for record in records:
        base = {
            "_input_address": record.get("_input_address"),
            "_input_zpid": record.get("_input_zpid"),
            "_input_url": record.get("_input_url"),
            "_which": record.get("_which"),
            "_fetched_at": record.get("_fetched_at"),
        }

        if "_error" in record:
            rows.append({**base, "_error": record.get("_error")})
            continue

        payload = record.get("payload")
        metadata = extract_scalar_metadata(payload)
        rows.append({**base, **metadata})

    return pd.DataFrame(rows)


def append_jsonl(path: str, records: list[dict[str, Any]]) -> None:
    """Append records to a JSONL file."""
    if not records:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def append_csv_rows(path: str, rows_df: pd.DataFrame, dedupe_subset: list[str] | None = None) -> None:
    """Append rows to CSV, with optional dedupe."""
    if rows_df.empty:
        return

    if os.path.exists(path):
        existing_df = pd.read_csv(path)
        combined = pd.concat([existing_df, rows_df], ignore_index=True)
        if dedupe_subset:
            dedupe_cols = [column for column in dedupe_subset if column in combined.columns]
            if dedupe_cols:
                combined = combined.drop_duplicates(subset=dedupe_cols, keep="last")
        combined.to_csv(path, index=False)
        return

    rows_df.to_csv(path, index=False)


def enrich_graphs(
    df: pd.DataFrame,
    api_key: str,
    raw_output_path: str = REALTYAPI_GRAPH_RAW_PATH,
    points_output_path: str = REALTYAPI_GRAPH_POINTS_PATH,
    summary_output_path: str = REALTYAPI_GRAPH_SUMMARY_PATH,
    which_values: tuple[str, ...] | list[str] = DEFAULT_WHICH,
    sleep_seconds: float = 0.2,
    force_refresh: bool = False,
) -> dict[str, int]:
    """Incrementally fetch graph histories and append new raw/derived rows."""
    normalized_df = normalize_input_frame(df)
    if normalized_df.empty:
        return {"input_rows": 0, "cached": 0, "fetched": 0, "success": 0, "errors": 0}

    cached = {} if force_refresh else load_existing_results(raw_output_path)
    records = dict(cached)
    pending: list[tuple[pd.Series, str]] = []
    ordered_keys: list[tuple[str, str]] = []
    which_values = tuple(which_values)

    for _, row in normalized_df.iterrows():
        address = row["address"]
        for which in which_values:
            key = (address, which)
            ordered_keys.append(key)
            if key not in records:
                pending.append((row, which))

    new_records: list[dict[str, Any]] = []
    session = requests.Session()
    try:
        for index, (row, which) in enumerate(pending, start=1):
            address = row["address"]
            print(f"[{index}/{len(pending)}] Fetching {which} for {address}")
            try:
                record = fetch_graph(session, row, which, api_key)
            except Exception as exc:  # noqa: BLE001
                record = {
                    "_input_address": address,
                    "_input_zpid": coerce_zpid(row.get("PropertyZPID")),
                    "_input_url": first_nonempty(row.get("PropertyZillowURL"), row.get("zillow_url")),
                    "_which": which,
                    "_fetched_at": pd.Timestamp.utcnow().isoformat(),
                    "_error": str(exc),
                }
                print(f"  Failed: {exc}")
            records[(address, which)] = record
            new_records.append(record)
            if sleep_seconds and index < len(pending):
                time.sleep(sleep_seconds)
    finally:
        session.close()

    append_jsonl(raw_output_path, new_records)

    if new_records:
        points_df = build_points_frame(new_records)
        summary_df = build_summary_frame(new_records)
        append_csv_rows(
            points_output_path,
            points_df,
            dedupe_subset=["_input_address", "_which", "_series_path", "_series_index", "x", "y"],
        )
        append_csv_rows(
            summary_output_path,
            summary_df,
            dedupe_subset=["_input_address", "_which"],
        )

    success_count = sum(1 for record in new_records if "_error" not in record)
    error_count = len(new_records) - success_count
    return {
        "input_rows": len(normalized_df),
        "cached": len(ordered_keys) - len(pending),
        "fetched": len(new_records),
        "success": success_count,
        "errors": error_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch RealtyAPI graph_charts histories")
    parser.add_argument("--input", default=REALTYAPI_ENRICHED_PATH, help="Input CSV path")
    parser.add_argument("--raw-output", default=REALTYAPI_GRAPH_RAW_PATH, help="Raw JSONL output path")
    parser.add_argument("--points-output", default=REALTYAPI_GRAPH_POINTS_PATH, help="Normalized CSV output path")
    parser.add_argument("--summary-output", default=REALTYAPI_GRAPH_SUMMARY_PATH, help="Per-response summary CSV output path")
    parser.add_argument(
        "--api-key",
        default=os.getenv("REALTYAPI_KEY") or DEFAULT_REALTYAPI_KEY,
        help="RealtyAPI key",
    )
    parser.add_argument("--limit", type=int, help="Only process the first N listings")
    parser.add_argument("--sleep-seconds", type=float, default=0.2, help="Delay between requests")
    parser.add_argument(
        "--which",
        nargs="+",
        default=list(DEFAULT_WHICH),
        choices=list(DEFAULT_WHICH),
        help="Graph history types to fetch",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore cached raw responses and refetch every graph",
    )
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("Missing RealtyAPI key. Use --api-key or set REALTYAPI_KEY.")

    df = pd.read_csv(args.input)
    if args.limit:
        df = df.head(args.limit).copy()
    stats = enrich_graphs(
        df=df,
        api_key=args.api_key,
        raw_output_path=args.raw_output,
        points_output_path=args.points_output,
        summary_output_path=args.summary_output,
        which_values=args.which,
        sleep_seconds=args.sleep_seconds,
        force_refresh=args.force_refresh,
    )
    print(f"Input rows: {stats['input_rows']}")
    print(f"History types: {', '.join(args.which)}")
    print(f"Cached graph responses: {stats['cached']}")
    print(f"Requests fetched: {stats['fetched']}")
    print(f"Saved raw responses: {args.raw_output}")
    print(f"Saved points CSV: {args.points_output}")
    print(f"Saved summary CSV: {args.summary_output}")
    print(f"Successful responses: {stats['success']}")
    print(f"Errored responses: {stats['errors']}")


if __name__ == "__main__":
    main()

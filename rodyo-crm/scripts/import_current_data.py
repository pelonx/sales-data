#!/usr/bin/env python3
"""
Import current V1 Google Sheet data into the RODYO CRM Supabase schema.

Run from the rodyo-crm directory:
  python3 scripts/import_current_data.py --dry-run
  python3 scripts/import_current_data.py

The script reads Supabase credentials from .env.local and uses public/exportable
Google Sheet CSV URLs for the current shared workbook.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import pandas as pd
import requests


DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/1kY5e6SXd7eQ7GJx-jg6M1R60WCCZ9I_25Eb7ZmuDKHw/edit?usp=sharing"
DEFAULT_MONTHLY_GID = "0"
DEFAULT_MASTER_STORE_GID = "1421425539"
DEFAULT_REP_GID = "1653796501"
DEFAULT_ORDER_SHEET_NAME = "Cultivera Data"
CONTACT_LOG_WORKSHEET = "Contact Log"
STORE_CONTACT_WORKSHEET = "Store Contacts"
SALES_GOALS_WORKSHEET = "Sales Goals"

TERRITORY_BRANDS = ["K. Savage", "Mayfield", "Leisure Land"]

MONTH_NUMS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
MONTH_ABBR = {
    1: "Jan",
    2: "Feb",
    3: "Mar",
    4: "Apr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Aug",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dec",
}
SHEET_ID_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")
TOTAL_PATTERN = re.compile(
    r"^(total|totals|sum|grand\s*total|ytd|year\s*to\s*date|annual|avg|average|subtotal)s?$",
    re.IGNORECASE,
)
NON_REVENUE_PATTERN = re.compile(
    r"(drop\s*date|date|notes?|comments?|status|category|type)$",
    re.IGNORECASE,
)
MONTH_PATTERN = re.compile(
    r"^(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december|q[1-4])(?:[\s._/-]*\d{2,4})?$",
    re.IGNORECASE,
)

TERRITORY_LOCATION_COLUMNS = [
    "License",
    "Store Name",
    "Address",
    "City",
    "State",
    "Zip",
    "Latitude",
    "Longitude",
    "Google Place ID",
    "Geocoded At",
    "Geocode Status",
    "License Type",
    "County",
    "Sales Last Month",
    "Sales Rank",
    "Flowers & Prerolls",
    "Concentrates & Cartridges",
    "Edibles, Topicals, Infused, etc.",
    "UBI",
]


class ImportErrorWithHint(RuntimeError):
    pass


@dataclass
class ImportContext:
    env: dict[str, str]
    dry_run: bool
    replace: bool
    supabase: "SupabaseRestClient | None"
    store_by_license_key: dict[str, dict[str, Any]]
    store_by_store_key: dict[str, dict[str, Any]]


class SupabaseRestClient:
    def __init__(self, url: str, key: str):
        self.base_url = url.rstrip("/") + "/rest/v1"
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        payload: Any = None,
        prefer: str = "return=minimal",
    ) -> Any:
        headers = {**self.headers, "Prefer": prefer}
        response = requests.request(
            method,
            f"{self.base_url}/{table}",
            headers=headers,
            params=params,
            data=json.dumps(payload) if payload is not None else None,
            timeout=60,
        )
        if response.status_code >= 400:
            raise ImportErrorWithHint(
                f"Supabase {method} {table} failed with HTTP {response.status_code}: {response.text[:700]}"
            )
        if not response.text:
            return None
        return response.json()

    def select(self, table: str, columns: str = "*", params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        query = {"select": columns, "limit": "10000"}
        if params:
            query.update(params)
        return self.request("GET", table, params=query) or []

    def upsert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        *,
        on_conflict: str,
        returning: bool = False,
        chunk_size: int = 500,
    ) -> list[dict[str, Any]]:
        if not rows:
            return []
        out: list[dict[str, Any]] = []
        prefer = "resolution=merge-duplicates,return=representation" if returning else "resolution=merge-duplicates,return=minimal"
        for chunk in chunks(rows, chunk_size):
            result = self.request(
                "POST",
                table,
                params={"on_conflict": on_conflict},
                payload=chunk,
                prefer=prefer,
            )
            if returning and result:
                out.extend(result)
        return out

    def insert(self, table: str, rows: list[dict[str, Any]], *, chunk_size: int = 500) -> None:
        if not rows:
            return
        for chunk in chunks(rows, chunk_size):
            self.request("POST", table, payload=chunk, prefer="return=minimal")

    def delete_all(self, table: str) -> None:
        self.request(
            "DELETE",
            table,
            params={"id": "not.is.null"},
            prefer="return=minimal",
        )


def chunks(values: list[dict[str, Any]], size: int):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = dict(os.environ)
    if not path.exists():
        return env
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        env[key] = value
    return env


def env_value(env: dict[str, str], key: str, default: str = "") -> str:
    return str(env.get(key, default) or "").strip()


def parse_amount(value: Any, strict: bool = False) -> float:
    cleaned = re.sub(r"[$,\s]", "", str(value or ""))
    if cleaned.lower() in {"", "nan", "none"}:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        if strict:
            raise ValueError(f"Could not parse numeric sales value: {value!r}")
        return 0.0


def clean_reference(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none"}:
        return ""
    try:
        number = float(text)
    except Exception:
        return text
    if number.is_integer():
        return str(int(number))
    return text


def clean_cell(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none"} else text


def license_match_key(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().upper()
    if text.lower() in {"", "nan", "none"}:
        return ""
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    text = re.sub(r"^(LICENSE|LIC)\s*#?\s*", "", text)
    text = re.sub(r"[^A-Z0-9]", "", text)
    text = re.sub(r"^(LICENSE|LIC)", "", text)
    if text.isdigit():
        return text.lstrip("0") or "0"
    return text


def store_match_key(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().upper()
    if text.lower() in {"", "nan", "none"}:
        return ""
    return re.sub(r"[^A-Z0-9]", "", text)


def normalize_year(year_text: str) -> int:
    year = int(year_text)
    if year < 100:
        return 2000 + year if year < 70 else 1900 + year
    return year


def parse_month_header(header: Any) -> tuple[int, int] | None:
    text = re.sub(r"\s+", " ", str(header).strip())
    text = re.sub(r"(\d{2,4})\.\d+$", r"\1", text)
    month_words = "|".join(MONTH_NUMS)
    patterns = [
        rf"^({month_words})[\s._/-]+(\d{{2,4}})$",
        rf"^(\d{{2,4}})[\s._/-]+({month_words})$",
    ]
    for pattern in patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        first, second = match.groups()
        if first.lower() in MONTH_NUMS:
            return MONTH_NUMS[first.lower()], normalize_year(second)
        return MONTH_NUMS[second.lower()], normalize_year(first)
    return None


def month_label_to_date(label: Any) -> str | None:
    parsed = parse_month_header(label)
    if parsed:
        month, year = parsed
        return f"{year:04d}-{month:02d}-01"
    text = str(label or "").strip()
    if not text:
        return None
    try:
        period = pd.Period(text, freq="M")
        return period.to_timestamp().date().isoformat()
    except Exception:
        return None


def canonical_month_label(label: Any) -> str:
    parsed = parse_month_header(label)
    if not parsed:
        return str(label or "").strip()
    month_num, year = parsed
    return f"{MONTH_ABBR[month_num]} {year}"


def normalize_month_headers(headers: list[Any]) -> list[str]:
    normalized = [str(h).strip() for h in headers]
    parsed = []
    for i, header in enumerate(normalized):
        parsed_header = parse_month_header(header)
        if parsed_header:
            month_num, year = parsed_header
            parsed.append((i, month_num, year))
    if not parsed:
        return normalized

    month_seq = [month_num for _, month_num, _ in parsed]
    years = {year for _, _, year in parsed}
    block_count = len(parsed) // 12
    if (
        block_count > 1
        and len(parsed) % 12 == 0
        and month_seq == list(range(1, 13)) * block_count
        and len(years) == 1
    ):
        end_year = next(iter(years))
        start_year = end_year - block_count + 1
        for order, (idx, month_num, _) in enumerate(parsed):
            normalized[idx] = f"{MONTH_ABBR[month_num]} {start_year + (order // 12)}"
        return normalized

    for idx, month_num, year in parsed:
        normalized[idx] = f"{MONTH_ABBR[month_num]} {year}"
    return normalized


def is_totals_col(header: str, values: list[float], other_cols: list[list[float]]) -> bool:
    header_text = str(header).strip()
    if TOTAL_PATTERN.match(header_text):
        return True
    if NON_REVENUE_PATTERN.search(header_text):
        return True
    if MONTH_PATTERN.match(header_text):
        return False
    if len(other_cols) < 2:
        return False
    if not any(abs(v) > 0.01 for v in values):
        return False
    row_sums = [sum(c[i] for c in other_cols) for i in range(len(values))]
    return all(
        abs(v - row_sums[i]) <= max(0.01, abs(row_sums[i]) * 0.001)
        for i, v in enumerate(values)
    )


def google_sheet_csv_url(sheet_url: str, gid: str = "0") -> str:
    parsed = urlparse(str(sheet_url or "").strip())
    qs = parse_qs(parsed.query)
    fragment_qs = parse_qs(parsed.fragment)
    url_gid = (
        qs.get("gid", [None])[0]
        or fragment_qs.get("gid", [None])[0]
        or str(gid or "0").strip()
        or "0"
    )
    if "docs.google.com" not in parsed.netloc:
        return str(sheet_url)
    match = SHEET_ID_PATTERN.search(parsed.path)
    if not match:
        raise ImportErrorWithHint(f"Could not find spreadsheet ID in {sheet_url}")
    sheet_id = match.group(1)
    base = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    return base if url_gid in ("0", "", None) else f"{base}&gid={url_gid}"


def google_sheet_csv_url_by_sheet_name(sheet_url: str, sheet_name: str) -> str:
    parsed = urlparse(str(sheet_url or "").strip())
    if "docs.google.com" not in parsed.netloc:
        raise ImportErrorWithHint("Sheet-name lookup requires a Google Sheets URL.")
    match = SHEET_ID_PATTERN.search(parsed.path)
    if not match:
        raise ImportErrorWithHint(f"Could not find spreadsheet ID in {sheet_url}")
    sheet_id = match.group(1)
    return (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq"
        f"?tqx=out:csv&sheet={quote(sheet_name)}"
    )


def fetch_sheet_df(csv_url: str, label: str) -> pd.DataFrame:
    response = requests.get(
        csv_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            )
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise ImportErrorWithHint(
            f"Could not fetch {label}. Google returned HTTP {response.status_code}. "
            "Confirm the workbook is shared or published for CSV access."
        )
    df = pd.read_csv(StringIO(response.text)).dropna(how="all").dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def first_source_col(df: pd.DataFrame, aliases: list[str]) -> str | None:
    source_cols = {str(c).strip().lower(): c for c in df.columns}
    for alias in aliases:
        found = source_cols.get(str(alias).strip().lower())
        if found is not None:
            return found
    return None


def coerce_coord(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if text.lower() in {"", "nan", "none"}:
        return None
    try:
        return float(text)
    except Exception:
        return None


def normalize_store_locations(raw_df: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "License": ["License", "License #", "license", "license_number"],
        "Store Name": ["Store Name", "Store", "Client", "Retailer", "Account", "Business Name"],
        "Address": ["Address", "Street Address", "Address 1", "Line 1", "Street"],
        "City": ["City", "Town"],
        "State": ["State", "Province", "Region"],
        "Zip": ["Zip", "ZIP", "Zip Code", "ZIP Code", "Zipcode", "Postal Code", "PostalCode", "Postal"],
        "Latitude": ["Latitude", "Lat", "lat"],
        "Longitude": ["Longitude", "Lng", "Lon", "Long", "longitude", "lng", "lon"],
        "Google Place ID": ["Google Place ID", "Place ID", "place_id", "google_place_id"],
        "Geocoded At": ["Geocoded At", "geocoded_at"],
        "Geocode Status": ["Geocode Status", "geocode_status", "Status"],
        "License Type": ["License Type", "license_type"],
        "County": ["County"],
        "Sales Last Month": ["Sales Last Month", "Market Sales Last Month", "Monthly Sales"],
        "Sales Rank": ["Sales Rank", "Rank"],
        "Flowers & Prerolls": ["Flowers & Prerolls", "Flower Rank", "Flowers and Prerolls"],
        "Concentrates & Cartridges": ["Concentrates & Cartridges", "Concentrate Rank"],
        "Edibles, Topicals, Infused, etc.": [
            "Edibles, Topicals, Infused, etc.",
            "Edibles Rank",
            "Topicals Rank",
            "Infused Rank",
        ],
        "UBI": ["UBI"],
    }
    out = pd.DataFrame(index=raw_df.index)
    for target, source_names in aliases.items():
        source = first_source_col(raw_df, source_names)
        out[target] = raw_df[source] if source is not None else ""
    for col in TERRITORY_LOCATION_COLUMNS:
        if col not in {"Latitude", "Longitude"}:
            out[col] = out[col].apply(clean_cell)
    out["Latitude"] = out["Latitude"].apply(coerce_coord)
    out["Longitude"] = out["Longitude"].apply(coerce_coord)
    out["License"] = out["License"].apply(clean_reference)
    out["Store Name"] = out["Store Name"].where(out["Store Name"].astype(str).str.strip().ne(""), out["License"])
    out["_license_key"] = out["License"].apply(license_match_key)
    out = out[out["_license_key"].ne("")]
    out = out.drop_duplicates("_license_key", keep="last").drop(columns=["_license_key"])
    return out[TERRITORY_LOCATION_COLUMNS].reset_index(drop=True)


def normalize_territory_rep_assignments(raw_df: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "License": ["License", "License #", "license", "license_number"],
        "Store Name": ["Store Name", "Store", "Client", "Retailer", "Account", "Business Name"],
        "Territory Rep": ["Territory Rep", "Sales Rep", "Rep", "Representative", "Owner", "Assigned Rep"],
        "Territory": ["Territory", "Region", "Sales Territory", "Area", "Route"],
    }
    out = pd.DataFrame(index=raw_df.index)
    for target, source_names in aliases.items():
        source = first_source_col(raw_df, source_names)
        out[target] = raw_df[source] if source is not None else ""
    for col in aliases:
        out[col] = out[col].apply(clean_cell)
    out["License"] = out["License"].apply(clean_reference)
    out["License Key"] = out["License"].apply(license_match_key)
    out["Store Key"] = out["Store Name"].apply(store_match_key)
    out = out[
        (out["License Key"].ne("") | out["Store Key"].ne(""))
        & (out["Territory Rep"].ne("") | out["Territory"].ne(""))
    ].copy()
    return out[["License", "Store Name", "Territory Rep", "Territory", "License Key", "Store Key"]].reset_index(drop=True)


def parse_monthly_revenue(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    if len(raw_df.columns) < 3:
        raise ImportErrorWithHint("Monthly sheet needs License, Store Name, and month columns.")
    headers = [str(c).strip() for c in raw_df.columns]
    raw_month_headers = normalize_month_headers(headers[2:])
    data = raw_df.fillna("")

    col_arrays: list[list[float]] = []
    for j in range(len(raw_month_headers)):
        source_col = raw_df.columns[j + 2]
        col_arrays.append([parse_amount(value) for value in data[source_col].tolist()])

    keep_indices = []
    for j, header in enumerate(raw_month_headers):
        others = [col_arrays[k] for k in range(len(col_arrays)) if k != j]
        if not is_totals_col(header, col_arrays[j], others):
            keep_indices.append(j)
    months = [raw_month_headers[j] for j in keep_indices]
    if not months:
        raise ImportErrorWithHint("No monthly revenue columns found.")

    records: list[dict[str, Any]] = []
    for _, row in data.iterrows():
        license_value = clean_reference(row.iloc[0])
        if not license_value:
            continue
        store_name = clean_cell(row.iloc[1]) or license_value
        record = {"License": license_value, "Store Name": store_name}
        for out_idx, source_idx in enumerate(keep_indices):
            record[months[out_idx]] = parse_amount(row.iloc[source_idx + 2])
        records.append(record)
    if not records:
        raise ImportErrorWithHint("No monthly revenue rows found.")

    agg: dict[str, dict[str, Any]] = {}
    for record in records:
        license_value = record["License"]
        if license_value not in agg:
            agg[license_value] = record.copy()
        else:
            for month in months:
                agg[license_value][month] = float(agg[license_value].get(month, 0)) + float(record.get(month, 0))
    df = pd.DataFrame(list(agg.values()))
    return df, months


def brand_from_sub_product_line(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("LL"):
        return "Leisure Land"
    if text.startswith("MF"):
        return "Mayfield"
    if text.startswith("KS"):
        return "K. Savage"
    if text.startswith("Bulk"):
        return "Bulk"
    return "Other"


def normalize_orders(raw_df: pd.DataFrame) -> pd.DataFrame:
    out = raw_df.copy()
    if "Sub Product Line" in out.columns:
        out["Brand"] = out["Sub Product Line"].apply(brand_from_sub_product_line)
    elif "Brand" not in out.columns:
        out["Brand"] = "Other"
    for date_col in ("Submitted Date", "Manifested Date", "Transfer Date", "Estimated delivery date"):
        if date_col in out.columns:
            out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
    license_col = first_source_col(out, ["License #", "License", "license_number"])
    if license_col:
        out["License #"] = out[license_col].apply(clean_reference)
    out = out.drop_duplicates().reset_index(drop=True)
    return out


def parse_date(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    try:
        ts = pd.to_datetime(value, errors="coerce")
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return ts.date().isoformat()


def parse_timestamp(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    try:
        ts = pd.to_datetime(value, errors="coerce")
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return ts.isoformat()


def json_safe(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def row_payload(row: pd.Series) -> dict[str, Any]:
    return {str(key): json_safe(value) for key, value in row.to_dict().items()}


def commitment_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"yes", "y", "true", "1"}


def build_supabase_client(env: dict[str, str], dry_run: bool) -> SupabaseRestClient | None:
    if dry_run:
        return None
    url = env_value(env, "NEXT_PUBLIC_SUPABASE_URL")
    key = env_value(env, "SUPABASE_SECRET_KEY") or env_value(env, "SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise ImportErrorWithHint(
            "Missing NEXT_PUBLIC_SUPABASE_URL or SUPABASE_SECRET_KEY in rodyo-crm/.env.local."
        )
    return SupabaseRestClient(url, key)


def require_client(ctx: ImportContext) -> SupabaseRestClient:
    if ctx.supabase is None:
        raise RuntimeError("Supabase client is not available in dry-run mode.")
    return ctx.supabase


def fetch_sources(env: dict[str, str]) -> dict[str, pd.DataFrame]:
    sheet_url = env_value(env, "IMPORT_GOOGLE_SHEET_URL", DEFAULT_SHEET_URL)
    monthly_gid = env_value(env, "IMPORT_MONTHLY_GID", DEFAULT_MONTHLY_GID)
    master_gid = env_value(env, "IMPORT_MASTER_STORE_GID", DEFAULT_MASTER_STORE_GID)
    rep_gid = env_value(env, "IMPORT_TERRITORY_REP_GID", DEFAULT_REP_GID)
    order_sheet_name = env_value(env, "IMPORT_ORDER_SHEET_NAME", DEFAULT_ORDER_SHEET_NAME)

    sources = {
        "master": fetch_sheet_df(google_sheet_csv_url(sheet_url, master_gid), "Top Shelf retailer list"),
        "monthly": fetch_sheet_df(google_sheet_csv_url(sheet_url, monthly_gid), "monthly revenue sheet"),
        "rep_assignments": fetch_sheet_df(google_sheet_csv_url(sheet_url, rep_gid), "territory rep assignments"),
        "orders": fetch_sheet_df(google_sheet_csv_url_by_sheet_name(sheet_url, order_sheet_name), "Cultivera order sheet"),
    }

    for key, worksheet in [
        ("contact_logs", CONTACT_LOG_WORKSHEET),
        ("store_contacts", STORE_CONTACT_WORKSHEET),
        ("sales_goals", SALES_GOALS_WORKSHEET),
    ]:
        try:
            sources[key] = fetch_sheet_df(google_sheet_csv_url_by_sheet_name(sheet_url, worksheet), worksheet)
        except Exception as exc:
            print(f"  Warning: skipped optional sheet {worksheet}: {exc}")
            sources[key] = pd.DataFrame()
    return sources


def clear_import_tables(ctx: ImportContext) -> None:
    if ctx.dry_run:
        return
    client = require_client(ctx)
    for table in [
        "sales_goals",
        "sample_drops",
        "store_contacts",
        "contact_logs",
        "order_items",
        "orders",
        "monthly_revenue",
        "store_locations",
        "stores",
        "regions",
        "reps",
    ]:
        print(f"  Clearing {table}...")
        client.delete_all(table)


def assert_safe_to_import(ctx: ImportContext) -> None:
    if ctx.dry_run:
        return
    client = require_client(ctx)
    counts = {}
    for table in ["stores", "monthly_revenue", "orders", "contact_logs"]:
        result = client.request(
            "GET",
            table,
            params={"select": "id", "limit": "1"},
        )
        counts[table] = len(result or [])
    if any(counts.values()) and not ctx.replace:
        raise ImportErrorWithHint(
            "Supabase already has imported-looking data. Re-run with --replace if you intentionally "
            "want to clear and reload CRM import tables."
        )


def refresh_store_maps(ctx: ImportContext) -> None:
    if ctx.dry_run:
        return
    client = require_client(ctx)
    rows = client.select("stores", "id,license,license_key,store_name,store_key")
    ctx.store_by_license_key = {
        str(row.get("license_key", "")): row
        for row in rows
        if str(row.get("license_key", ""))
    }
    ctx.store_by_store_key = {
        str(row.get("store_key", "")): row
        for row in rows
        if str(row.get("store_key", ""))
    }


def upsert_stores_from_locations(ctx: ImportContext, locations: pd.DataFrame) -> int:
    rows = []
    for _, row in locations.iterrows():
        license_value = clean_reference(row.get("License"))
        license_key = license_match_key(license_value)
        if not license_key:
            continue
        store_name = clean_cell(row.get("Store Name")) or license_value
        rows.append({
            "license": license_value,
            "license_key": license_key,
            "store_name": store_name,
            "store_key": store_match_key(store_name),
            "license_type": clean_cell(row.get("License Type")),
            "ubi": clean_cell(row.get("UBI")),
            "source_name": "Top Shelf Data",
        })
    if not ctx.dry_run:
        require_client(ctx).upsert("stores", rows, on_conflict="license_key")
        refresh_store_maps(ctx)
    return len(rows)


def upsert_stores_from_monthly(ctx: ImportContext, monthly_df: pd.DataFrame) -> int:
    rows = []
    known = set(ctx.store_by_license_key)
    for _, row in monthly_df.iterrows():
        license_value = clean_reference(row.get("License"))
        license_key = license_match_key(license_value)
        if not license_key or license_key in known:
            continue
        store_name = clean_cell(row.get("Store Name")) or license_value
        rows.append({
            "license": license_value,
            "license_key": license_key,
            "store_name": store_name,
            "store_key": store_match_key(store_name),
            "source_name": "monthly_sheet",
        })
    if rows and not ctx.dry_run:
        require_client(ctx).upsert("stores", rows, on_conflict="license_key")
        refresh_store_maps(ctx)
    return len(rows)


def import_store_locations(ctx: ImportContext, locations: pd.DataFrame) -> int:
    rows = []
    for _, row in locations.iterrows():
        store = ctx.store_by_license_key.get(license_match_key(row.get("License")))
        if not store:
            continue
        rows.append({
            "store_id": store["id"],
            "address": clean_cell(row.get("Address")),
            "city": clean_cell(row.get("City")),
            "state": clean_cell(row.get("State")) or "WA",
            "zip": clean_cell(row.get("Zip")),
            "county": clean_cell(row.get("County")),
            "latitude": coerce_coord(row.get("Latitude")),
            "longitude": coerce_coord(row.get("Longitude")),
            "google_place_id": clean_cell(row.get("Google Place ID")),
            "geocode_status": clean_cell(row.get("Geocode Status")),
            "geocoded_at": parse_timestamp(row.get("Geocoded At")),
            "market_sales_last_month": parse_amount(row.get("Sales Last Month")),
            "market_sales_rank": parse_amount(row.get("Sales Rank")),
            "flowers_prerolls": clean_cell(row.get("Flowers & Prerolls")),
            "concentrates_cartridges": clean_cell(row.get("Concentrates & Cartridges")),
            "edibles_topicals_infused": clean_cell(row.get("Edibles, Topicals, Infused, etc.")),
        })
    if rows and not ctx.dry_run:
        require_client(ctx).upsert("store_locations", rows, on_conflict="store_id")
    return len(rows)


def import_rep_assignments(ctx: ImportContext, assignments: pd.DataFrame) -> int:
    rep_names = sorted({clean_cell(v) for v in assignments.get("Territory Rep", []) if clean_cell(v)})
    region_names = sorted({clean_cell(v) for v in assignments.get("Territory", []) if clean_cell(v)})
    if not ctx.dry_run:
        client = require_client(ctx)
        client.upsert("reps", [{"initials": rep, "name": rep} for rep in rep_names], on_conflict="initials")
        client.upsert("regions", [{"name": region} for region in region_names], on_conflict="name")
        reps = {row["initials"]: row for row in client.select("reps", "id,initials")}
        regions = {row["name"]: row for row in client.select("regions", "id,name")}
        updates = []
        for _, row in assignments.iterrows():
            store = ctx.store_by_license_key.get(row.get("License Key")) or ctx.store_by_store_key.get(row.get("Store Key"))
            if not store:
                continue
            rep = reps.get(clean_cell(row.get("Territory Rep")))
            region = regions.get(clean_cell(row.get("Territory")))
            updates.append({
                "id": store["id"],
                "license": store["license"],
                "license_key": store["license_key"],
                "store_name": store["store_name"],
                "store_key": store.get("store_key"),
                "rep_id": rep["id"] if rep else None,
                "region_id": region["id"] if region else None,
            })
        if updates:
            client.upsert("stores", updates, on_conflict="id")
            refresh_store_maps(ctx)
    return len(assignments)


def import_monthly_revenue(ctx: ImportContext, monthly_df: pd.DataFrame, months: list[str]) -> int:
    rows = []
    monthly_by_key = {
        license_match_key(row.get("License")): row
        for _, row in monthly_df.iterrows()
        if license_match_key(row.get("License"))
    }
    for license_key, store in ctx.store_by_license_key.items():
        source_row = monthly_by_key.get(license_key)
        for month in months:
            revenue_month = month_label_to_date(month)
            if not revenue_month:
                continue
            rows.append({
                "store_id": store["id"],
                "revenue_month": revenue_month,
                "revenue": parse_amount(source_row.get(month)) if source_row is not None else 0,
                "source_name": "monthly_sheet",
            })
    if rows and not ctx.dry_run:
        require_client(ctx).upsert("monthly_revenue", rows, on_conflict="store_id,revenue_month")
    return len(rows)


def import_orders(ctx: ImportContext, orders_df: pd.DataFrame) -> tuple[int, int, int]:
    order_number_col = first_source_col(orders_df, ["Order #", "Order Number", "Order"])
    client_col = first_source_col(orders_df, ["Client", "Store Name", "Customer"])
    license_col = first_source_col(orders_df, ["License #", "License"])
    submitted_col = first_source_col(orders_df, ["Submitted Date", "Order Date", "Date"])
    status_col = first_source_col(orders_df, ["Status", "Order Status"])
    product_col = first_source_col(orders_df, ["Product", "Product Name", "Inventory Name", "Item"])
    sub_product_col = first_source_col(orders_df, ["Sub Product Line", "Subproduct Line"])
    units_col = first_source_col(orders_df, ["Units", "Quantity", "Qty"])
    line_total_col = first_source_col(orders_df, ["Line Total", "Sales", "Total"])
    if not order_number_col or not license_col:
        raise ImportErrorWithHint("Cultivera orders need at least Order # and License # columns.")

    order_rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    skipped = 0
    for _, row in orders_df.iterrows():
        order_number = clean_reference(row.get(order_number_col))
        license_value = clean_reference(row.get(license_col))
        license_key = license_match_key(license_value)
        if not order_number:
            skipped += 1
            continue
        key = (order_number, license_key)
        if key not in order_rows_by_key:
            store = ctx.store_by_license_key.get(license_key)
            order_rows_by_key[key] = {
                "order_number": order_number,
                "store_id": store["id"] if store else None,
                "client_name": clean_cell(row.get(client_col)) if client_col else "",
                "license": license_value,
                "license_key": license_key,
                "submitted_at": parse_timestamp(row.get(submitted_col)) if submitted_col else None,
                "status": clean_cell(row.get(status_col)) if status_col else "",
                "source_name": "cultivera",
                "raw_payload": row_payload(row),
            }

    order_rows = list(order_rows_by_key.values())
    item_rows = []
    if not ctx.dry_run:
        client = require_client(ctx)
        client.upsert("orders", order_rows, on_conflict="order_number,license_key")
        imported_orders = {
            (row["order_number"], row.get("license_key") or ""): row
            for row in client.select("orders", "id,order_number,license_key")
        }
        for _, row in orders_df.iterrows():
            order_number = clean_reference(row.get(order_number_col))
            license_key = license_match_key(row.get(license_col))
            order = imported_orders.get((order_number, license_key))
            if not order:
                continue
            item_rows.append({
                "order_id": order["id"],
                "brand": clean_cell(row.get("Brand")) or brand_from_sub_product_line(row.get(sub_product_col)) if sub_product_col else clean_cell(row.get("Brand")) or "Other",
                "product_name": clean_cell(row.get(product_col)) if product_col else "",
                "sub_product_line": clean_cell(row.get(sub_product_col)) if sub_product_col else "",
                "units": parse_amount(row.get(units_col)) if units_col else 0,
                "line_total": parse_amount(row.get(line_total_col)) if line_total_col else 0,
                "raw_payload": row_payload(row),
            })
        if item_rows:
            client.insert("order_items", item_rows)
    else:
        item_rows = [{} for _ in range(len(orders_df))]
    return len(order_rows), len(item_rows), skipped


def normalize_contact_log(raw_df: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "License": ["License", "license"],
        "Store Name": ["Store Name", "store_name", "Client", "Store"],
        "Month": ["Month", "contact_month"],
        "Revenue": ["Revenue", "revenue"],
        "Date Contacted": ["Date Contacted", "date_contacted"],
        "Commitment": ["Commitment", "Commitment Made", "commitment_made"],
        "Cadence": ["Cadence", "Committed Cadence", "committed_cadence"],
        "Committed Amount": ["Committed Amount", "Committed $ Amount", "Amount", "committed_amount"],
        "Notes": ["Notes", "notes"],
        "Initials": ["Initials", "initials"],
        "Person Contacted": ["Person Contacted", "person_contacted"],
        "Contact Method": ["Contact Method", "contact_method"],
        "Next Outreach": ["Next Outreach", "Next Outreach Alert", "next_outreach"],
        "Next Outreach Date": ["Next Outreach Date", "Next Outreach Alert Date", "next_outreach_date"],
        "Alert Recipient": ["Alert Recipient", "Alert Email", "alert_recipient"],
        "Alert CC": ["Alert CC", "alert_cc"],
        "Alert Sent Week": ["Alert Sent Week", "alert_sent_week"],
        "Saved At": ["Saved At", "saved_at"],
    }
    out = pd.DataFrame(index=raw_df.index)
    for target, names in aliases.items():
        source = first_source_col(raw_df, names)
        out[target] = raw_df[source] if source is not None else ""
    out = out.fillna("").astype(str)
    out["License"] = out["License"].str.strip()
    out["Store Name"] = out["Store Name"].str.strip()
    out["Month"] = out["Month"].apply(canonical_month_label)
    out = out[(out["License"].ne("")) & (out["Store Name"].ne(""))]
    return out


def import_contact_logs(ctx: ImportContext, raw_df: pd.DataFrame) -> int:
    if raw_df.empty:
        return 0
    logs = normalize_contact_log(raw_df)
    rows = []
    for _, row in logs.iterrows():
        license_key = license_match_key(row.get("License"))
        store = ctx.store_by_license_key.get(license_key) or ctx.store_by_store_key.get(store_match_key(row.get("Store Name")))
        rows.append({
            "store_id": store["id"] if store else None,
            "license": clean_cell(row.get("License")),
            "license_key": license_key,
            "store_name": clean_cell(row.get("Store Name")),
            "contact_month": month_label_to_date(row.get("Month")),
            "revenue_label": clean_cell(row.get("Revenue")),
            "date_contacted": parse_date(row.get("Date Contacted")),
            "commitment_made": commitment_bool(row.get("Commitment")),
            "committed_cadence": clean_cell(row.get("Cadence")),
            "committed_amount": clean_cell(row.get("Committed Amount")),
            "notes": clean_cell(row.get("Notes")),
            "initials": clean_cell(row.get("Initials")),
            "person_contacted": clean_cell(row.get("Person Contacted")),
            "contact_method": clean_cell(row.get("Contact Method")),
            "next_outreach": clean_cell(row.get("Next Outreach")),
            "next_outreach_date": parse_date(row.get("Next Outreach Date")),
            "alert_recipient": clean_cell(row.get("Alert Recipient")),
            "alert_cc": clean_cell(row.get("Alert CC")),
            "alert_sent_week": clean_cell(row.get("Alert Sent Week")),
            "saved_at": parse_timestamp(row.get("Saved At")) or datetime.now().isoformat(),
        })
    if rows and not ctx.dry_run:
        require_client(ctx).insert("contact_logs", rows)
    return len(rows)


def import_store_contacts(ctx: ImportContext, raw_df: pd.DataFrame) -> int:
    if raw_df.empty:
        return 0
    aliases = {
        "License": ["License", "license"],
        "Store Name": ["Store Name", "store_name", "Client", "Store"],
        "Contact Name": ["Contact Name", "contact_name", "Name"],
        "Phone Number": ["Phone Number", "phone_number", "Phone", "phone"],
        "Email": ["Email", "email"],
    }
    out = pd.DataFrame(index=raw_df.index)
    for target, names in aliases.items():
        source = first_source_col(raw_df, names)
        out[target] = raw_df[source] if source is not None else ""
    rows = []
    for _, row in out.iterrows():
        license_key = license_match_key(row.get("License"))
        store = ctx.store_by_license_key.get(license_key) or ctx.store_by_store_key.get(store_match_key(row.get("Store Name")))
        if not store:
            continue
        rows.append({
            "store_id": store["id"],
            "contact_name": clean_cell(row.get("Contact Name")),
            "phone_number": clean_cell(row.get("Phone Number")),
            "email": clean_cell(row.get("Email")),
        })
    if rows and not ctx.dry_run:
        require_client(ctx).upsert("store_contacts", rows, on_conflict="store_id")
    return len(rows)


def import_sales_goals(ctx: ImportContext, raw_df: pd.DataFrame) -> int:
    if raw_df.empty:
        return 0
    aliases = {
        "Month": ["Month", "month", "Month Key"],
        "Goal Type": ["Goal Type", "goal_type", "Type"],
        "Week ID": ["Week ID", "week_id", "Week Start"],
        "Week": ["Week", "Week Label", "week_label"],
        "Brand": ["Brand", "brand"],
        "Goal": ["Goal", "goal", "Amount", "amount"],
        "Notes": ["Notes", "notes", "Note"],
        "Updated At": ["Updated At", "updated_at", "Saved At"],
    }
    out = pd.DataFrame(index=raw_df.index)
    for target, names in aliases.items():
        source = first_source_col(raw_df, names)
        out[target] = raw_df[source] if source is not None else ""
    rows = []
    for _, row in out.iterrows():
        goal_month = month_label_to_date(row.get("Month"))
        if not goal_month:
            continue
        rows.append({
            "goal_month": goal_month,
            "goal_type": clean_cell(row.get("Goal Type")),
            "week_id": clean_cell(row.get("Week ID")),
            "week_label": clean_cell(row.get("Week")),
            "brand": clean_cell(row.get("Brand")),
            "goal_amount": parse_amount(row.get("Goal")),
            "notes": clean_cell(row.get("Notes")),
            "updated_at": parse_timestamp(row.get("Updated At")) or datetime.now().isoformat(),
        })
    if rows and not ctx.dry_run:
        require_client(ctx).insert("sales_goals", rows)
    return len(rows)


def run_import(args: argparse.Namespace) -> None:
    project_dir = Path(__file__).resolve().parents[1]
    env = load_env(project_dir / ".env.local")
    print("Loading Google Sheet sources...")
    sources = fetch_sources(env)

    master_locations = normalize_store_locations(sources["master"])
    monthly_df, months = parse_monthly_revenue(sources["monthly"])
    rep_assignments = normalize_territory_rep_assignments(sources["rep_assignments"])
    orders_df = normalize_orders(sources["orders"])

    print("\nSource preview:")
    print(f"  Master retailer rows: {len(master_locations):,}")
    print(f"  Monthly revenue stores: {len(monthly_df):,}")
    print(f"  Monthly revenue months: {len(months):,} ({months[0]} -> {months[-1]})")
    print(f"  Territory assignment rows: {len(rep_assignments):,}")
    print(f"  Cultivera order detail rows: {len(orders_df):,}")
    print(f"  Contact log rows: {len(sources['contact_logs']):,}")
    print(f"  Store contact rows: {len(sources['store_contacts']):,}")
    print(f"  Sales goal rows: {len(sources['sales_goals']):,}")

    ctx = ImportContext(
        env=env,
        dry_run=bool(args.dry_run),
        replace=bool(args.replace),
        supabase=build_supabase_client(env, bool(args.dry_run)),
        store_by_license_key={},
        store_by_store_key={},
    )

    if args.dry_run:
        print("\nDry run complete. No Supabase data was changed.")
        return

    assert_safe_to_import(ctx)
    if args.replace:
        print("\nReplace mode enabled. Clearing import tables...")
        clear_import_tables(ctx)

    print("\nImporting to Supabase...")
    imported_master = upsert_stores_from_locations(ctx, master_locations)
    print(f"  Stores from master list: {imported_master:,}")
    refresh_store_maps(ctx)
    imported_extra = upsert_stores_from_monthly(ctx, monthly_df)
    if imported_extra:
        print(f"  Extra stores from monthly revenue: {imported_extra:,}")
    imported_locations = import_store_locations(ctx, master_locations)
    print(f"  Store locations: {imported_locations:,}")
    imported_assignments = import_rep_assignments(ctx, rep_assignments)
    print(f"  Rep assignments processed: {imported_assignments:,}")
    imported_revenue = import_monthly_revenue(ctx, monthly_df, months)
    print(f"  Monthly revenue rows: {imported_revenue:,}")
    imported_orders, imported_items, skipped_orders = import_orders(ctx, orders_df)
    print(f"  Orders: {imported_orders:,}; order items: {imported_items:,}; skipped order rows: {skipped_orders:,}")
    imported_logs = import_contact_logs(ctx, sources["contact_logs"])
    print(f"  Contact logs: {imported_logs:,}")
    imported_contacts = import_store_contacts(ctx, sources["store_contacts"])
    print(f"  Store contacts: {imported_contacts:,}")
    imported_goals = import_sales_goals(ctx, sources["sales_goals"])
    print(f"  Sales goals: {imported_goals:,}")

    client = require_client(ctx)
    rollup_counts = client.request(
        "GET",
        "crm_store_rollup",
        params={"select": "map_category", "limit": "10000"},
    ) or []
    category_counts: dict[str, int] = {}
    for row in rollup_counts:
        category = str(row.get("map_category") or "Uncategorized")
        category_counts[category] = category_counts.get(category, 0) + 1

    print("\nImport complete. Store category counts:")
    for category, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {category}: {count:,}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Import V1 dashboard data into Supabase.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse sources without writing to Supabase.")
    parser.add_argument("--replace", action="store_true", help="Clear import tables before loading data.")
    args = parser.parse_args()
    try:
        run_import(args)
    except ImportErrorWithHint as exc:
        print(f"\nImport stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

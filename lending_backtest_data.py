#!/usr/bin/env python3
"""Guarded collector for Financial Services Commission stock-lending data.

The portal page confirms dataset ID 15059612 and the operation descriptions.
The detailed DOCX guide could not be retrieved in this run. Therefore the
operation/field contract below is provisional and is accepted only after a
live response passes strict field, date-filter and pagination checks.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from requests.exceptions import RequestException

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "lending"
OUTPUT_DIR = ROOT / "output"
DATA_FILE = DATA_DIR / "stock_lending_daily.csv.gz"
STATUS_FILE = OUTPUT_DIR / "lending_collection_status.csv"
SUMMARY_FILE = OUTPUT_DIR / "lending_collection_summary.txt"

BASE_URL = "https://apis.data.go.kr/1160100/service"
SERVICE = "GetStocLendBorrInfoService_V2"
OPERATION = "getStItemLendAndBorrStatu_V2"
EXPECTED_FIELDS = {
    "basDt", "isinCd", "isinCdNm",
    "lnbCclStckCnt", "lnbRdptStckCnt", "lnbRmanStckCnt",
}
START_DEFAULT = "20260501"
PAGE_SIZE = max(1, min(int(os.getenv("LENDING_PAGE_SIZE", "100")), 1000))
REQUEST_INTERVAL = max(0.5, float(os.getenv("LENDING_REQUEST_INTERVAL", "1.0")))
TIMEOUT = (10, 45)
KST = ZoneInfo("Asia/Seoul")

# Keep transport libraries from logging request URLs, which contain serviceKey.
import logging
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)


class LendingAPIError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def kst_now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def date_range(start: date, end: date):
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, compression="gzip", dtype=str, low_memory=False)


def extract_items(body: dict) -> list[dict]:
    items = body.get("items")
    if not isinstance(items, dict):
        return []
    item = items.get("item", [])
    if item is None:
        return []
    if isinstance(item, dict):
        return [item]
    if isinstance(item, list):
        return [x for x in item if isinstance(x, dict)]
    return []


def parse_response(payload: dict, requested_date: str) -> tuple[int, list[dict], str]:
    response = payload.get("response")
    if not isinstance(response, dict):
        # Some gateway errors use OpenAPI_ServiceResponse rather than response.
        service_response = payload.get("OpenAPI_ServiceResponse", {})
        header = service_response.get("cmmMsgHeader", {}) if isinstance(service_response, dict) else {}
        code = str(header.get("returnReasonCode") or header.get("errCode") or "UNKNOWN")
        raise LendingAPIError(code, "API gateway returned an error envelope.")

    header = response.get("header") or {}
    code = str(header.get("resultCode", "UNKNOWN")).zfill(2)
    message = str(header.get("resultMsg", ""))

    if code == "03":
        return 0, [], "NO_DATA"
    if code != "00":
        descriptions = {
            "01": "gateway application error",
            "02": "upstream database error",
            "04": "HTTP or upstream response error",
            "05": "upstream timeout",
            "10": "invalid request parameter",
            "11": "required parameter missing",
            "12": "unknown or retired service operation",
            "20": "API permission denied or service key missing",
            "22": "daily API quota exceeded",
            "23": "per-second API quota exceeded",
            "29": "source IP blocked",
            "30": "service key not registered",
            "31": "service key expired",
            "32": "source IP not registered",
            "33": "unsigned request",
        }
        raise LendingAPIError(code, descriptions.get(code, "API returned a non-success result code"))

    body = response.get("body") or {}
    try:
        total = int(body.get("totalCount", 0))
    except (TypeError, ValueError):
        raise LendingAPIError("SCHEMA", "API response is missing a numeric totalCount.")

    rows = extract_items(body)
    if total > 0 and not rows:
        raise LendingAPIError("SCHEMA", "API reports rows but returned no item records.")

    for row in rows:
        missing = EXPECTED_FIELDS - set(row)
        if missing:
            raise LendingAPIError("SCHEMA", "Live response is missing expected stock-lending fields.")
        if str(row.get("basDt", "")) != requested_date:
            raise LendingAPIError(
                "FILTER",
                "The requested basDt filter was not applied; refusing to store unfiltered rows.",
            )
    return total, rows, message


class LendingClient:
    def __init__(self, api_key: str):
        self.api_key = unquote(api_key.strip())
        self.session = requests.Session()
        self.last_request = 0.0

    def _wait(self):
        remaining = REQUEST_INTERVAL - (time.monotonic() - self.last_request)
        if remaining > 0:
            time.sleep(remaining)

    def get_page(self, bas_dt: str, page_no: int) -> tuple[int, list[dict]]:
        params = {
            "serviceKey": self.api_key,
            "numOfRows": PAGE_SIZE,
            "pageNo": page_no,
            "resultType": "json",
            "basDt": bas_dt,
        }
        url = f"{BASE_URL}/{SERVICE}/{OPERATION}"
        last_code = "NETWORK"
        for attempt in range(1, 4):
            self._wait()
            try:
                response = self.session.get(url, params=params, timeout=TIMEOUT)
                self.last_request = time.monotonic()
            except RequestException:
                last_code = "NETWORK"
                if attempt < 3:
                    time.sleep(attempt * 2)
                    continue
                raise LendingAPIError(last_code, "Network request failed after three attempts.")

            if response.status_code == 429 or response.status_code >= 500:
                last_code = f"HTTP_{response.status_code}"
                if attempt < 3:
                    time.sleep(attempt * 2)
                    continue
                raise LendingAPIError(last_code, "Temporary HTTP error after three attempts.")
            try:
                payload = response.json()
            except ValueError:
                raise LendingAPIError(
                    f"HTTP_{response.status_code}",
                    "API response was not JSON; response body was withheld from logs.",
                )
            try:
                total, rows, _ = parse_response(payload, bas_dt)
                return total, rows
            except LendingAPIError as exc:
                if exc.code in {"01", "02", "04", "05", "23"} and attempt < 3:
                    time.sleep(attempt * 3)
                    continue
                raise
        raise LendingAPIError(last_code, "API request failed.")

    def probe(self, end: date) -> str:
        # Validate the live endpoint/filter against a recent trading date before
        # starting a multi-thousand-page backfill.
        for offset in range(0, 15):
            candidate = end - timedelta(days=offset)
            if candidate.weekday() >= 5:
                continue
            day = candidate.strftime("%Y%m%d")
            total, rows = self.get_page(day, 1)
            if total > 0 and rows:
                valid_isin = any(re.fullmatch(r"KR[A-Z0-9]{10}", str(x.get("isinCd", ""))) for x in rows)
                if not valid_isin:
                    raise LendingAPIError("SCHEMA", "No valid 12-character Korean ISIN found in the live sample.")
                return day
        raise LendingAPIError(
            "PROBE",
            "No recent date returned rows; operation, date filter, approval and history range remain unverified.",
        )


def fetch_date(client: LendingClient, day: str) -> tuple[list[dict], int, int]:
    total, first = client.get_page(day, 1)
    if total == 0:
        return [], 0, 0
    pages = max(1, math.ceil(total / PAGE_SIZE))
    all_rows = list(first)
    for page_no in range(2, pages + 1):
        page_total, rows = client.get_page(day, page_no)
        if page_total != total:
            raise LendingAPIError("PAGINATION", "totalCount changed while paging; date was not committed.")
        if not rows:
            raise LendingAPIError("PAGINATION", f"Page {page_no} was empty before totalCount was reached.")
        all_rows.extend(rows)
    if len(all_rows) != total:
        raise LendingAPIError("PAGINATION", "Fetched row count does not match totalCount.")
    collected_at = kst_now()
    for row in all_rows:
        row["_collected_at"] = collected_at
        row["_source_service"] = SERVICE
        row["_source_operation"] = OPERATION
    return all_rows, total, pages


def save_data(fresh_rows: list[dict]) -> tuple[int, int, int, int, str, str]:
    existing = load_csv(DATA_FILE)
    fresh = pd.DataFrame(fresh_rows)
    if existing.empty and fresh.empty:
        return 0, 0, 0, 0, "", ""
    combined = pd.concat([existing, fresh], ignore_index=True, sort=False)
    before = len(combined)
    if {"basDt", "isinCd"}.issubset(combined.columns):
        combined = combined.drop_duplicates(subset=["basDt", "isinCd"], keep="last")
    duplicates_removed = before - len(combined)
    sort_cols = [c for c in ("basDt", "isinCd") if c in combined.columns]
    if sort_cols:
        combined = combined.sort_values(sort_cols, kind="stable")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_csv(DATA_FILE, index=False, compression="gzip", encoding="utf-8")
    if "isinCd" in combined.columns:
        valid_isin_rows = int(combined["isinCd"].astype(str).str.fullmatch(r"KR[A-Z0-9]{10}", na=False).sum())
    else:
        valid_isin_rows = 0
    missing_fields = [field for field in EXPECTED_FIELDS if field not in combined.columns]
    if missing_fields:
        missing_required = len(combined)
    else:
        missing_required = int(combined[list(EXPECTED_FIELDS)].isna().any(axis=1).sum())
    dates = combined["basDt"].dropna().astype(str) if "basDt" in combined.columns else pd.Series(dtype=str)
    return (
        len(combined),
        valid_isin_rows,
        missing_required,
        duplicates_removed,
        dates.min() if not dates.empty else "",
        dates.max() if not dates.empty else "",
    )


def save_status(status_rows: list[dict]):
    old = load_csv(STATUS_FILE)
    new = pd.DataFrame(status_rows)
    if old.empty:
        merged = new
    elif new.empty:
        merged = old
    else:
        merged = pd.concat([old, new], ignore_index=True, sort=False)
        merged = merged.drop_duplicates(subset=["date"], keep="last")
    if not merged.empty:
        merged = merged.sort_values("date")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    merged.to_csv(STATUS_FILE, index=False, encoding="utf-8-sig")


def main() -> int:
    raw_key = os.getenv("KRX_LENDING_API_KEY", "").strip()
    if not raw_key:
        raise LendingAPIError("KEY_MISSING", "KRX_LENDING_API_KEY is not configured.")

    mode = os.getenv("SHORTING_MODE", "backfill").lower()
    end = parse_date(os.getenv("SHORTING_END_DATE") or date.today().strftime("%Y%m%d"))
    requested_start = parse_date(os.getenv("SHORTING_START_DATE", START_DEFAULT))
    if requested_start > end:
        raise LendingAPIError("DATE_RANGE", "Lending start date is after end date.")

    existing_status = load_csv(STATUS_FILE)
    status_map = {}
    if not existing_status.empty and {"date", "status"}.issubset(existing_status.columns):
        status_map = {
            str(row["date"]): str(row["status"])
            for _, row in existing_status.iterrows()
        }

    if mode == "backfill":
        start = requested_start
    elif mode == "incremental":
        all_days = [d.strftime("%Y%m%d") for d in date_range(requested_start, end)]
        missing_or_failed = [
            d for d in all_days
            if status_map.get(d) not in {"ok", "empty_or_no_data"}
        ]
        start = parse_date(min(missing_or_failed)) if missing_or_failed else max(
            requested_start, end - timedelta(days=7)
        )
    else:
        raise LendingAPIError("MODE", "SHORTING_MODE must be backfill or incremental.")

    client = LendingClient(raw_key)
    probe_date = client.probe(end)
    print(f"[LENDING] live response validated for basDt={probe_date}; beginning {mode} collection.", flush=True)

    fresh_rows: list[dict] = []
    new_status: list[dict] = []
    failed = False
    refresh_cutoff = end - timedelta(days=7)

    for day_obj in date_range(start, end):
        day = day_obj.strftime("%Y%m%d")
        if mode == "incremental" and day_obj < refresh_cutoff and status_map.get(day) in {"ok", "empty_or_no_data"}:
            continue
        try:
            rows, total, pages = fetch_date(client, day)
            new_status.append({
                "date": day, "rows": len(rows), "total_count": total, "pages": pages,
                "status": "ok" if rows else "empty_or_no_data",
                "error_code": "", "error_message": "", "collected_at": kst_now(),
            })
            fresh_rows.extend(rows)
            print(f"[LENDING] {day}: rows={len(rows)} pages={pages}", flush=True)
        except LendingAPIError as exc:
            new_status.append({
                "date": day, "rows": 0, "total_count": "", "pages": 0,
                "status": "failed", "error_code": exc.code,
                "error_message": str(exc)[:300], "collected_at": kst_now(),
            })
            print(f"[LENDING][STOP] date={day} code={exc.code}: {exc}", flush=True)
            failed = True
            break

    save_status(new_status)
    total_rows, valid_isin_rows, missing_required, duplicates_removed, first_date, last_date = save_data(fresh_rows)
    summary = (
        "금융위원회 주식대차 데이터 수집 결과\n"
        f"모드: {mode}\n요청 시작일: {start:%Y%m%d}\n종료일: {end:%Y%m%d}\n"
        f"검증용 실데이터 기준일: {probe_date}\n"
        f"이번 실행 신규 수집 행: {len(fresh_rows):,}\n"
        f"누적 행: {total_rows:,}\n"
        f"유효 ISIN 행: {valid_isin_rows:,}\n"
        f"필수 필드 결측 행: {missing_required:,}\n"
        f"중복 제거 행: {duplicates_removed:,}\n"
        f"누적 기준일 범위: {first_date or '없음'} ~ {last_date or '없음'}\n"
        f"상태: {'부분 수집/중단' if failed else '요청 범위 처리 완료'}\n"
        "주의: 대차잔고는 공매도 잔고와 다른 지표이며, lnbRmanStckCnt는 API 응답의 잔여 대차 주식 수 필드입니다.\n"
        "주의: 공식 활용자가이드 DOCX의 원문 명세는 별도 대조가 필요합니다. 실시간 응답의 필드·기준일 필터·페이지 수 검증을 통과한 행만 저장합니다.\n"
        "이용조건: 공공누리 제2유형(출처표시, 상업적 이용금지). 상업적 이용은 한국예탁결제원과 별도 계약이 필요합니다.\n"
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_FILE.write_text(summary, encoding="utf-8")
    print(summary, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LendingAPIError as exc:
        print(f"[LENDING][FATAL] code={exc.code}: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2)
    except Exception:
        # Do not print arbitrary HTTP exceptions or request URLs: serviceKey is
        # transmitted in the query string.
        print("[LENDING][FATAL] unexpected error; details withheld to protect API credentials.", file=sys.stderr, flush=True)
        raise SystemExit(3)

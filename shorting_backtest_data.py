#!/usr/bin/env python3
"""Collect KRX short-selling, short-balance and daily-price history.

Raw KRX columns are retained. Query metadata is added so that the source schema
can be inspected before building the backtest. KRX short-balance data is
reported with a T+2 lag and may be absent for recent dates.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from krx_data_api import fetch

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "shorting"
OUTPUT_DIR = ROOT / "output"
START_DEFAULT = "20260501"
MARKETS = {
    "STK": "1",   # KOSPI
    "KSQ": "2",   # KOSDAQ
    "KNX": "6",   # KONEX
}
FILES = {
    "short_volume": DATA_DIR / "short_volume.csv.gz",
    "short_balance": DATA_DIR / "short_balance.csv.gz",
    "daily_prices": DATA_DIR / "daily_prices.csv.gz",
}


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def date_range(start: date, end: date):
    current = start
    while current <= end:
        # Weekends are skipped; exchange holidays return an empty KRX response.
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def fetch_retry(endpoint: str, **params) -> pd.DataFrame:
    last_error = None
    for attempt in range(1, 4):
        try:
            return fetch(endpoint, auth=True, **params)
        except Exception as exc:
            last_error = exc
            print(
                f"[WARN] {endpoint} {params} attempt {attempt}/3 failed: {exc}",
                flush=True,
            )
            if attempt < 3:
                time.sleep(attempt * 3)
    raise RuntimeError(f"KRX request failed after 3 attempts: {endpoint} {params}") from last_error


def add_query_metadata(df: pd.DataFrame, day: str, market: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out["_query_date"] = day
    out["_query_market"] = market
    return out


def load_existing(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, compression="gzip", dtype=str, low_memory=False)


def code_column(columns) -> str | None:
    candidates = (
        "ISU_CD", "isuCd", "ISU_SRT_CD", "isuSrtCd",
        "종목코드", "단축코드", "표준코드", "종목코드(단축)",
    )
    for name in candidates:
        if name in columns:
            return name
    return None


def merge_and_save(path: Path, fresh_frames: list[pd.DataFrame]) -> int:
    frames = [load_existing(path)] if path.exists() else []
    frames.extend(frame for frame in fresh_frames if frame is not None and not frame.empty)
    if not frames:
        return 0

    combined = pd.concat(frames, ignore_index=True, sort=False)
    keys = ["_query_date", "_query_market"]
    code = code_column(combined.columns)
    if code:
        keys.append(code)
    else:
        print(f"[WARN] No recognizable ticker-code column in {path.name}; deduplicating full rows.")
        keys = None

    before = len(combined)
    combined = combined.drop_duplicates(subset=keys, keep="last") if keys else combined.drop_duplicates()
    combined = combined.sort_values(
        [c for c in ("_query_date", "_query_market") if c in combined.columns]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(path, index=False, compression="gzip", encoding="utf-8")
    return len(combined)


def main() -> int:
    if not os.getenv("KRX_ID") or not os.getenv("KRX_PW"):
        raise RuntimeError("KRX_ID and KRX_PW must be configured as GitHub Actions secrets.")

    mode = os.getenv("SHORTING_MODE", "backfill").lower()
    end = parse_date(os.getenv("SHORTING_END_DATE") or date.today().strftime("%Y%m%d"))
    configured_start = parse_date(os.getenv("SHORTING_START_DATE", START_DEFAULT))

    if mode == "incremental":
        # Re-fetch the recent 10 calendar days to fill the KRX T+2 balance lag
        # and retry dates that may have been temporarily unavailable.
        start = max(configured_start, end - timedelta(days=10))
    elif mode == "backfill":
        start = configured_start
    else:
        raise ValueError("SHORTING_MODE must be 'backfill' or 'incremental'.")

    if start > end:
        raise ValueError(f"Start date {start} is after end date {end}.")

    print(f"Collection mode={mode}, range={start:%Y%m%d}..{end:%Y%m%d}", flush=True)

    volume_frames: list[pd.DataFrame] = []
    balance_frames: list[pd.DataFrame] = []
    price_frames: list[pd.DataFrame] = []
    status: list[dict[str, str]] = []
    total_calls = 0

    for day in date_range(start, end):
        day_text = day.strftime("%Y%m%d")
        print(f"[DATE] {day_text}", flush=True)

        # Daily short-selling transactions, separately by market.
        for market in MARKETS:
            df = fetch_retry("short_selling_individual", trdDd=day_text, mktId=market)
            total_calls += 1
            tagged = add_query_metadata(df, day_text, market)
            if not tagged.empty:
                volume_frames.append(tagged)
            status.append({
                "date": day_text, "dataset": "short_volume", "market": market,
                "rows": str(len(tagged)), "status": "ok" if not tagged.empty else "empty_or_holiday",
            })

        # Daily net short-position balances; these are normally published T+2.
        for market, market_code in MARKETS.items():
            df = fetch_retry(
                "short_balance_individual", trdDd=day_text, mktTpCd=market_code
            )
            total_calls += 1
            tagged = add_query_metadata(df, day_text, market)
            if not tagged.empty:
                balance_frames.append(tagged)
            status.append({
                "date": day_text, "dataset": "short_balance", "market": market,
                "rows": str(len(tagged)), "status": "ok" if not tagged.empty else "not_yet_reported_or_empty",
            })

        # All-market daily OHLCV snapshot, to align price movement with short activity.
        df = fetch_retry("all_stock_price", trdDd=day_text, mktId="ALL")
        total_calls += 1
        tagged = add_query_metadata(df, day_text, "ALL")
        if not tagged.empty:
            price_frames.append(tagged)
        status.append({
            "date": day_text, "dataset": "daily_prices", "market": "ALL",
            "rows": str(len(tagged)), "status": "ok" if not tagged.empty else "empty_or_holiday",
        })

    volume_count = merge_and_save(FILES["short_volume"], volume_frames)
    balance_count = merge_and_save(FILES["short_balance"], balance_frames)
    price_count = merge_and_save(FILES["daily_prices"], price_frames)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    status_path = OUTPUT_DIR / "shorting_collection_status.csv"
    pd.DataFrame(status).to_csv(status_path, index=False, encoding="utf-8-sig")

    summary = (
        "KRX 공매도 백테스트 데이터 수집 결과\n"
        f"모드: {mode}\n기간: {start:%Y-%m-%d} ~ {end:%Y-%m-%d}\n"
        f"KRX 요청 수: {total_calls}\n"
        f"공매도 거래 누적 행: {volume_count:,}\n"
        f"공매도 잔고 누적 행: {balance_count:,}\n"
        f"전종목 주가 누적 행: {price_count:,}\n"
        "시장: KOSPI / KOSDAQ / KONEX\n"
        "참고: 공매도 순보유잔고는 보고 기준에 따른 T+2 지연 데이터이며, "
        "보고의무 기준 미만 잔고는 포함되지 않을 수 있습니다.\n"
    )
    summary_path = OUTPUT_DIR / "shorting_collection_summary.txt"
    summary_path.write_text(summary, encoding="utf-8")
    print(summary, flush=True)

    # Send a concise completion message through the repository's existing Telegram helper.
    try:
        from Alerts import emit_alert
        emit_alert("공매도 백테스트 데이터 수집 완료", summary)
    except Exception as exc:
        print(f"[WARN] Telegram notification was not sent: {exc}", flush=True)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr, flush=True)
        raise

#!/usr/bin/env python3
"""Build a daily order-flow research panel from market data and account snapshot data.

This stage focuses on observable flow: short-selling flow, price, and account
position snapshot. It intentionally records missing market/account fields as
missing values rather than zeros so the user can distinguish 'not observed'
from 'zero'.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SHORT_DIR = DATA_DIR / "shorting"
RESEARCH_DIR = DATA_DIR / "research"
OUTPUT_DIR = ROOT / "output"
PANEL_PATH = RESEARCH_DIR / "nh_orderflow_daily.csv.gz"
SUMMARY_PATH = OUTPUT_DIR / "nh_orderflow_summary.txt"

# Account snapshot helper is optional and intentionally never logs the account number.
try:
    from nh_account_snapshot import fetch_domestic_balance_snapshot, normalize_domestic_positions
except Exception:  # pragma: no cover
    fetch_domestic_balance_snapshot = None
    normalize_domestic_positions = None

DATE_CANDIDATES = ("_query_date", "basDt", "BAS_DT", "TRD_DD", "trdDd", "DATE", "date")
CODE_CANDIDATES = ("ISU_SRT_CD", "isuSrtCd", "ISU_CD", "isuCd", "종목코드", "종목코드(단축)")
NAME_CANDIDATES = ("ISU_ABBRV", "ISU_NM", "isuNm", "종목명", "name")
PRICE_CANDIDATES = ("TDD_CLSPRC", "CLSPRC", "close", "Close", "stck_clpr", "종가")
SHORT_VOLUME_CANDIDATES = ("CVSRTSELL_TRDVOL", "SHT_SELL_TRDVOL", "short_volume", "total_volume")
SHORT_BALANCE_CANDIDATES = ("BAL_QTY", "short_balance", "balance_qty")


def read_gzip_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, compression="gzip", dtype=str, low_memory=False)
    except Exception:
        return pd.DataFrame()


def normalize_date(value) -> str | None:
    if pd.isna(value):
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits[:8] if len(digits) >= 8 else None


def num(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False).str.strip()
    text = text.replace({"": pd.NA, "-": pd.NA, "nan": pd.NA, "None": pd.NA})
    return pd.to_numeric(text, errors="coerce")


def pick_column(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    for name in candidates:
        if name in df.columns:
            return name
    return None


def normalize_symbol(value: object) -> str:
    text = str(value or "").strip().replace(".0", "")
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    return text.upper()


def normalize_name(value: object) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def account_snapshot_positions() -> pd.DataFrame:
    if fetch_domestic_balance_snapshot is None or normalize_domestic_positions is None:
        return pd.DataFrame(columns=["instrument_id", "qty", "avg_price", "current_price", "name"])
    try:
        data = fetch_domestic_balance_snapshot()
    except Exception:
        return pd.DataFrame(columns=["instrument_id", "qty", "avg_price", "current_price", "name"])
    raw = normalize_domestic_positions(data)
    if not raw:
        return pd.DataFrame(columns=["instrument_id", "qty", "avg_price", "current_price", "name"])
    df = pd.DataFrame(raw)
    df["instrument_id"] = df["symbol"].map(normalize_symbol)
    df["qty"] = num(df["qty"])
    df["avg_price"] = num(df["avg_price"])
    df["current_price"] = num(df["current_price"])
    return df[["instrument_id", "name", "qty", "avg_price", "current_price"]].dropna(subset=["instrument_id"]).reset_index(drop=True)


def short_panel() -> pd.DataFrame:
    volume = read_gzip_csv(SHORT_DIR / "short_volume.csv.gz")
    balance = read_gzip_csv(SHORT_DIR / "short_balance.csv.gz")
    prices = read_gzip_csv(SHORT_DIR / "daily_prices.csv.gz")
    frames = []
    for name, df in [("volume", volume), ("balance", balance), ("prices", prices)]:
        if df.empty:
            continue
        date_col = pick_column(df, DATE_CANDIDATES)
        code_col = pick_column(df, CODE_CANDIDATES)
        if not date_col or not code_col:
            continue
        work = df[[date_col, code_col]].copy()
        work["trade_date"] = work[date_col].map(normalize_date)
        work["instrument_id"] = work[code_col].map(normalize_symbol)
        work = work.dropna(subset=["trade_date", "instrument_id"])
        if name == "volume":
            metric_col = pick_column(df, SHORT_VOLUME_CANDIDATES)
            if metric_col:
                work["short_volume"] = num(df[metric_col])
        elif name == "balance":
            metric_col = pick_column(df, SHORT_BALANCE_CANDIDATES)
            if metric_col:
                work["short_balance"] = num(df[metric_col])
        else:
            metric_col = pick_column(df, PRICE_CANDIDATES)
            if metric_col:
                work["close"] = num(df[metric_col])
        frames.append(work)

    if not frames:
        return pd.DataFrame(columns=["trade_date", "instrument_id", "short_volume", "short_balance", "close"])

    panel = frames[0]
    for other in frames[1:]:
        panel = panel.merge(other, on=["trade_date", "instrument_id"], how="outer", validate="one_to_one")
    for col in ["short_volume", "short_balance", "close"]:
        if col not in panel.columns:
            panel[col] = pd.NA
    return panel[["trade_date", "instrument_id", "short_volume", "short_balance", "close"]].reset_index(drop=True)


def build_panel() -> pd.DataFrame:
    market = short_panel()
    account = account_snapshot_positions()
    panel = market.copy()
    if account.empty:
        panel["account_qty"] = pd.NA
        panel["account_avg_price"] = pd.NA
        panel["account_current_price"] = pd.NA
        return panel

    account = account.rename(columns={"qty": "account_qty", "avg_price": "account_avg_price", "current_price": "account_current_price"})
    panel = panel.merge(account[["instrument_id", "account_qty", "account_avg_price", "account_current_price"]], on="instrument_id", how="left")
    return panel.sort_values(["trade_date", "instrument_id"], kind="stable").reset_index(drop=True)


def summary_text(panel: pd.DataFrame) -> str:
    lines = [
        "NH 계좌 수급·시장 수급 통합 리서치 패널",
        "===================================",
        f"생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"행 수: {len(panel):,}",
        f"종목 수: {panel['instrument_id'].nunique() if 'instrument_id' in panel.columns else 0:,}",
        f"기준일 범위: {panel['trade_date'].min() if 'trade_date' in panel.columns and not panel.empty else '없음'} ~ {panel['trade_date'].max() if 'trade_date' in panel.columns and not panel.empty else '없음'}",
        "",
        "주의: 자료 없음은 0이 아니라 조회되지 않은 값입니다. 실제 API가 없거나 응답이 비어 있으면 결측으로 보관합니다.",
        "주의: 시장 데이터와 계좌 데이터는 서로 다른 데이터셋입니다. 계좌 수량은 보유 포지션, 시장 데이터는 종목 전체 수급 흐름입니다.",
    ]
    if not panel.empty:
        account_count = int(panel["account_qty"].notna().sum()) if "account_qty" in panel.columns else 0
        short_volume_count = int(panel["short_volume"].notna().sum()) if "short_volume" in panel.columns else 0
        short_balance_count = int(panel["short_balance"].notna().sum()) if "short_balance" in panel.columns else 0
        lines.append(f"계좌 스냅샷 보유 종목: {account_count:,}")
        lines.append(f"공매도 거래량 데이터: {short_volume_count:,}")
        lines.append(f"공매도 잔고 데이터: {short_balance_count:,}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily order-flow research panel builder")
    parser.add_argument("--dry-run", action="store_true", help="Use existing repo data only and skip account snapshot fetch.")
    args = parser.parse_args()

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        # Force account snapshot skip by temporarily masking helper call.
        # This is intentionally conservative: market data still builds even when the
        # personal account snapshot is absent.
        global fetch_domestic_balance_snapshot
        fetch_domestic_balance_snapshot = None

    panel = build_panel()
    if panel.empty:
        summary = summary_text(panel)
        SUMMARY_PATH.write_text(summary, encoding="utf-8")
        print(summary, flush=True)
        return 0

    panel.to_csv(PANEL_PATH, index=False, compression="gzip", encoding="utf-8")
    SUMMARY_PATH.write_text(summary_text(panel), encoding="utf-8")
    print(summary_text(panel), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

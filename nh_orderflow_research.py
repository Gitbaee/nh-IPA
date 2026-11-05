#!/usr/bin/env python3
"""Build a daily order-flow research panel from market data and account snapshot data.

This stage focuses on observable flow: short-selling flow, price, and account
position snapshot. It intentionally records missing market/account fields as
missing values rather than zeros so the user can distinguish 'not observed'
from 'zero'.

Recent 5-observation momentum is included as a descriptive feature: close-to-
close return across the most recent five available observations (not necessarily
five calendar days). Missing/zero starting prices remain missing.
"""
from __future__ import annotations

import argparse
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

try:
    from nh_account_snapshot import fetch_domestic_balance_snapshot, normalize_domestic_positions
except Exception:  # pragma: no cover - market research continues without account link
    fetch_domestic_balance_snapshot = None
    normalize_domestic_positions = None

DATE_CANDIDATES = ("_query_date", "basDt", "BAS_DT", "TRD_DD", "trdDd", "DATE", "date")
CODE_CANDIDATES = ("ISU_SRT_CD", "isuSrtCd", "ISU_CD", "isuCd", "종목코드", "종목코드(단축)")
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
    return next((name for name in candidates if name in df.columns), None)


def normalize_symbol(value: object) -> str:
    text = str(value or "").strip().replace(".0", "")
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text.upper()


def account_snapshot_positions() -> pd.DataFrame:
    columns = ["instrument_id", "qty", "avg_price", "current_price"]
    if fetch_domestic_balance_snapshot is None or normalize_domestic_positions is None:
        return pd.DataFrame(columns=columns)
    try:
        raw = normalize_domestic_positions(fetch_domestic_balance_snapshot())
    except Exception:
        # Private account/API failure must not block market-data analysis.
        return pd.DataFrame(columns=columns)
    if not raw:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(raw)
    df["instrument_id"] = df["symbol"].map(normalize_symbol)
    for col in ("qty", "avg_price", "current_price"):
        df[col] = num(df[col])
    return df[["instrument_id", "qty", "avg_price", "current_price"]].dropna(subset=["instrument_id"])


def load_market_panel() -> pd.DataFrame:
    sources = [
        ("short_volume", read_gzip_csv(SHORT_DIR / "short_volume.csv.gz"), SHORT_VOLUME_CANDIDATES),
        ("short_balance", read_gzip_csv(SHORT_DIR / "short_balance.csv.gz"), SHORT_BALANCE_CANDIDATES),
        ("close", read_gzip_csv(SHORT_DIR / "daily_prices.csv.gz"), PRICE_CANDIDATES),
    ]
    panel = pd.DataFrame(columns=["trade_date", "instrument_id"])
    for output, frame, candidates in sources:
        if frame.empty:
            continue
        date_col = pick_column(frame, DATE_CANDIDATES)
        code_col = pick_column(frame, CODE_CANDIDATES)
        value_col = pick_column(frame, candidates)
        if not date_col or not code_col or not value_col:
            continue
        item = pd.DataFrame({
            "trade_date": frame[date_col].map(normalize_date),
            "instrument_id": frame[code_col].map(normalize_symbol),
            output: num(frame[value_col]),
        }).dropna(subset=["trade_date", "instrument_id"])
        # Avoid arbitrary aggregation or row selection if the source has conflicting duplicates.
        duplicates = item.duplicated(["trade_date", "instrument_id"], keep=False)
        if duplicates.any():
            item = item.loc[~duplicates]
        panel = item if panel.empty else panel.merge(
            item, on=["trade_date", "instrument_id"], how="outer", validate="one_to_one"
        )
    for col in ("short_volume", "short_balance", "close"):
        if col not in panel:
            panel[col] = pd.NA
    return panel


def add_recent_momentum(panel: pd.DataFrame) -> pd.DataFrame:
    """Add the latest 5-observation price momentum, ordered by instrument/date."""
    if panel.empty:
        panel["momentum_5obs_pct"] = pd.Series(dtype="float64")
        return panel
    panel = panel.sort_values(["instrument_id", "trade_date"], kind="stable").reset_index(drop=True)
    panel["close"] = num(panel["close"])
    grouped = panel.groupby("instrument_id", sort=False)["close"]
    start = grouped.shift(5)
    # Five elapsed observations means compare today's close to the close five
    # observations earlier; if insufficient history or start is zero, keep NA.
    panel["momentum_5obs_pct"] = ((panel["close"] / start) - 1.0) * 100.0
    panel.loc[start.isna() | start.eq(0) | panel["close"].isna(), "momentum_5obs_pct"] = pd.NA
    return panel


def build_panel() -> pd.DataFrame:
    panel = add_recent_momentum(load_market_panel())
    account = account_snapshot_positions()
    if account.empty:
        panel["account_qty"] = pd.NA
        panel["account_avg_price"] = pd.NA
        panel["account_current_price"] = pd.NA
    elif not panel.empty:
        account = account.rename(columns={
            "qty": "account_qty", "avg_price": "account_avg_price", "current_price": "account_current_price"
        })
        panel = panel.merge(account, on="instrument_id", how="left", validate="many_to_one")
    return panel


def summary_text(panel: pd.DataFrame) -> str:
    lines = [
        "NH 계좌 수급·시장 수급 통합 리서치 패널",
        "===================================",
        f"생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"행 수: {len(panel):,}",
        f"종목 수: {panel['instrument_id'].nunique() if 'instrument_id' in panel.columns else 0:,}",
        f"기준일 범위: {panel['trade_date'].min() if 'trade_date' in panel.columns and not panel.empty else '없음'} ~ {panel['trade_date'].max() if 'trade_date' in panel.columns and not panel.empty else '없음'}",
        "최근 5일 모멘텀: 최근 5개 관측 간 종가 변화율(거래일 기준; 자료/기준가 부족 시 결측).",
        "자료 없음은 0이 아니라 조회되지 않은 값입니다. 시장 데이터와 계좌 데이터는 구분해 보관합니다.",
    ]
    if not panel.empty:
        for col, label in (("account_qty", "계좌 스냅샷"), ("short_volume", "공매도 거래량"), ("short_balance", "공매도 잔고"), ("momentum_5obs_pct", "5일 모멘텀")):
            count = int(panel[col].notna().sum()) if col in panel else 0
            lines.append(f"{label} 관측 행: {count:,}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily order-flow research panel with recent momentum")
    parser.add_argument("--dry-run", action="store_true", help="Use committed market data only; skip account API.")
    args = parser.parse_args()
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        global fetch_domestic_balance_snapshot
        fetch_domestic_balance_snapshot = None
    panel = build_panel()
    if not panel.empty:
        panel.to_csv(PANEL_PATH, index=False, compression="gzip", encoding="utf-8")
    report = summary_text(panel)
    SUMMARY_PATH.write_text(report, encoding="utf-8")
    print(report, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Analyze already-collected KRX short-selling datasets for the valid date ranges.

This script reads the compressed CSV files already saved under data/shorting and
summarizes:
- which dates are available in each dataset
- which date ranges overlap across datasets
- top short-volume and short-balance names for the most recent valid day
- a quick price-move review for the most recent trading day

It does not fetch new data from KRX; it only analyzes what has already been
stored in the repository.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "shorting"
OUTPUT_DIR = ROOT / "output"

VOLUME_FILE = DATA_DIR / "short_volume.csv.gz"
BALANCE_FILE = DATA_DIR / "short_balance.csv.gz"
PRICE_FILE = DATA_DIR / "daily_prices.csv.gz"
SUMMARY_FILE = OUTPUT_DIR / "shorting_analysis_summary.txt"
TOP_CSV = OUTPUT_DIR / "shorting_analysis_top_symbols.csv"


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, compression="gzip", dtype=str, low_memory=False)


def to_number(series: pd.Series) -> pd.Series:
    if series.empty:
        return pd.Series([], dtype="float64")
    cleaned = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace("-", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce")


def pick_column(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    return None


def normalize_date_series(series: pd.Series) -> pd.Series:
    out = series.astype(str).str.strip()
    # Common forms: YYYYMMDD, YYYY-MM-DD, YYYY/MM/DD, or '_query_date' kept as string.
    out = out.str.replace(r"[^0-9]", "", regex=True)
    out = out.where(out.str.len() >= 8, pd.NA)
    return out.str[:8]


def build_date_summary(df: pd.DataFrame, label: str) -> tuple[list[str], str | None, str | None]:
    if df.empty:
        return [], None, None
    date_col = pick_column(
        df,
        ["_query_date", "TRD_DD", "trdDd", "BAS_DT", "basDt", "DATE", "date"],
    )
    if not date_col:
        return [], None, None
    clean_dates = normalize_date_series(df[date_col])
    valid_dates = sorted(clean_dates.dropna().unique().tolist())
    if not valid_dates:
        return [], None, None
    return valid_dates, valid_dates[0], valid_dates[-1]


def is_valid_isu_code(value: object) -> bool:
    text = str(value).strip()
    if not text:
        return False
    if re.fullmatch(r"KR[A-Z0-9]{10}", text):
        return True
    return bool(re.fullmatch(r"[A-Z0-9]{1,12}", text))


def pick_code_col(df: pd.DataFrame) -> str | None:
    candidates = [
        "ISU_CD",
        "ISU_SRT_CD",
        "isuCd",
        "isuSrtCd",
        "ISU_ABBRV",
        "ISU_NM",
        "종목코드",
        "종목코드(단축)",
        "code",
    ]
    for name in candidates:
        if name in df.columns:
            return name
    for col in df.columns:
        if col.lower().endswith("cd") or col.lower().endswith("code"):
            sample = df[col].dropna().astype(str).head(20)
            if sample.map(is_valid_isu_code).mean() >= 0.7:
                return col
    return None


def top_symbols_for_day(df: pd.DataFrame, metric_col: str, code_col: str, date_col: str, day: str, title: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["date", "code", "name", "metric"])

    work = df.copy()
    work[date_col] = normalize_date_series(work[date_col])
    work = work[work[date_col] == day].copy()
    if work.empty:
        return pd.DataFrame(columns=["date", "code", "name", "metric"])

    work["metric"] = to_number(work[metric_col]).fillna(0.0)
    work = work[work["metric"].notna()].copy()
    if work.empty:
        return pd.DataFrame(columns=["date", "code", "name", "metric"])

    sort_col = "metric"
    if "ISU_ABBRV" in work.columns:
        work["name"] = work["ISU_ABBRV"].astype(str)
    else:
        work["name"] = work.get(code_col, pd.Series([""] * len(work)))

    result = work[[date_col, code_col, "name", "metric"]].copy()
    result = result.rename(columns={date_col: "date", code_col: "code"})
    result = result.sort_values(sort_col, ascending=False).head(10)
    return result.reset_index(drop=True)


def latest_common_day(volume_dates: list[str], balance_dates: list[str], price_dates: list[str]) -> str | None:
    sets = [set(volume_dates), set(balance_dates), set(price_dates)]
    if not sets[0] or not sets[1] or not sets[2]:
        return None
    common = set.intersection(*sets)
    if not common:
        return None
    return max(sorted(common))


def main() -> int:
    volume = read_csv(VOLUME_FILE)
    balance = read_csv(BALANCE_FILE)
    prices = read_csv(PRICE_FILE)

    volume_dates, v_start, v_end = build_date_summary(volume, "volume")
    balance_dates, b_start, b_end = build_date_summary(balance, "balance")
    price_dates, p_start, p_end = build_date_summary(prices, "prices")

    common_day = latest_common_day(volume_dates, balance_dates, price_dates)

    volume_top = pd.DataFrame(columns=["date", "code", "name", "metric"])
    balance_top = pd.DataFrame(columns=["date", "code", "name", "metric"])
    price_top = pd.DataFrame(columns=["date", "code", "name", "metric"])

    if common_day:
        v_code = pick_code_col(volume) or "ISU_CD"
        b_code = pick_code_col(balance) or "ISU_CD"
        p_code = pick_code_col(prices) or "ISU_CD"
        v_date = pick_column(volume, ["_query_date", "TRD_DD", "trdDd", "DATE", "date"]) or "_query_date"
        b_date = pick_column(balance, ["_query_date", "TRD_DD", "trdDd", "DATE", "date"]) or "_query_date"
        p_date = pick_column(prices, ["_query_date", "TRD_DD", "trdDd", "DATE", "date"]) or "_query_date"

        v_metric = pick_column(volume, ["CVSRTSELL_TRDVOL", "ACC_TRDVOL", "total_volume"]) or "CVSRTSELL_TRDVOL"
        b_metric = pick_column(balance, ["BAL_QTY", "BAL_AMT", "BAL_RTO", "balance_qty"]) or "BAL_QTY"
        p_metric = pick_column(prices, ["FLUC_RT", "TDD_CLSPRC", "ACC_TRDVOL"]) or "FLUC_RT"

        volume_top = top_symbols_for_day(volume, v_metric, v_code, v_date, common_day, "short_volume")
        balance_top = top_symbols_for_day(balance, b_metric, b_code, b_date, common_day, "short_balance")
        price_top = top_symbols_for_day(prices, p_metric, p_code, p_date, common_day, "daily_prices")

    summary_lines = [
        "KRX 수집 데이터 분석 결과",
        "========================",
        "",
        f"volume 파일: {len(volume_dates)}개 날짜, {len(volume):,} rows, 범위 {v_start} ~ {v_end}",
        f"balance 파일: {len(balance_dates)}개 날짜, {len(balance):,} rows, 범위 {b_start} ~ {b_end}",
        f"price 파일: {len(price_dates)}개 날짜, {len(prices):,} rows, 범위 {p_start} ~ {p_end}",
        f"공통 유효 날짜: {len(set(volume_dates) & set(balance_dates) & set(price_dates))}개",
        f"최종 공통 일자: {common_day or '없음'}",
        "",
        "최근 공통 일자 기준 상위 종목",
    ]

    if common_day:
        summary_lines.extend(
            [
                "",
                "[거래량 상위 10]",
            ]
        )
        if volume_top.empty:
            summary_lines.append("- 수집된 거래량 데이터가 없습니다.")
        else:
            for _, row in volume_top.iterrows():
                summary_lines.append(
                    f"- {row['code']} ({row['name']}): {float(row['metric']):,.0f}"
                )

        summary_lines.extend(["", "[잔고 상위 10]"])
        if balance_top.empty:
            summary_lines.append("- 수집된 잔고 데이터가 없습니다.")
        else:
            for _, row in balance_top.iterrows():
                summary_lines.append(
                    f"- {row['code']} ({row['name']}): {float(row['metric']):,.0f}"
                )

        summary_lines.extend(["", "[가격 변동 상위 10]"])
        if price_top.empty:
            summary_lines.append("- 수집된 가격 데이터가 없습니다.")
        else:
            for _, row in price_top.iterrows():
                summary_lines.append(
                    f"- {row['code']} ({row['name']}): {float(row['metric']):,.3f}"
                )
    else:
        summary_lines.append("- 공통 날짜가 없어서 종목별 비교를 수행하지 못했습니다.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_FILE.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    combined_rows = []
    for name, frame in [("volume", volume_top), ("balance", balance_top), ("price", price_top)]:
        if not frame.empty:
            frame = frame.copy()
            frame["dataset"] = name
            combined_rows.append(frame)

    if combined_rows:
        pd.concat(combined_rows, ignore_index=True).to_csv(TOP_CSV, index=False, encoding="utf-8-sig")

    print("\n".join(summary_lines), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

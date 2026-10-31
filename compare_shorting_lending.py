#!/usr/bin/env python3
"""Build a conservative ISIN/date join of stock lending and KRX short data.

No ticker-name joins or synthetic identifiers are used. If a reliable ISIN
cannot be found directly or via a date-specific KRX price crosswalk, the
comparison is withheld and the reason is written to the summary.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
LENDING_FILE = DATA / "lending" / "stock_lending_daily.csv.gz"
SHORT_VOLUME_FILE = DATA / "shorting" / "short_volume.csv.gz"
SHORT_BALANCE_FILE = DATA / "shorting" / "short_balance.csv.gz"
PRICE_FILE = DATA / "shorting" / "daily_prices.csv.gz"
OUTPUT_FILE = DATA / "shorting_lending" / "shorting_lending_comparison.csv.gz"
SUMMARY_FILE = ROOT / "output" / "shorting_lending_comparison_summary.txt"

ISIN_CANDIDATES = ("isinCd", "ISIN_CD", "ISIN", "ISU_CD", "isuCd", "ISU_SRT_CD", "isuSrtCd")
CODE_CANDIDATES = ("ISU_SRT_CD", "isuSrtCd", "srtnCd", "SRtnCd", "ISU_CD", "isuCd")
DATE_CANDIDATES = ("_query_date", "basDt", "BAS_DT", "TRD_DD", "trdDd", "TRD_DD")


def read_data(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, compression="gzip", dtype=str, low_memory=False)


def date_key(value) -> str | None:
    if pd.isna(value):
        return None
    digits = re.sub(r"[^0-9]", "", str(value))
    return digits[:8] if len(digits) >= 8 else None


def is_isin(value) -> bool:
    return bool(re.fullmatch(r"KR[A-Z0-9]{10}", str(value).strip()))


def find_isin_column(df: pd.DataFrame) -> str | None:
    for col in ISIN_CANDIDATES:
        if col not in df.columns:
            continue
        sample = df[col].dropna().astype(str).head(500)
        if len(sample) and sample.map(is_isin).mean() >= 0.5:
            return col
    return None


def find_code_column(df: pd.DataFrame) -> str | None:
    for col in CODE_CANDIDATES:
        if col not in df.columns:
            continue
        sample = df[col].dropna().astype(str).str.strip().head(500)
        if len(sample) and sample.str.fullmatch(r"\d{6}").mean() >= 0.5:
            return col
    return None


def find_date_column(df: pd.DataFrame) -> str | None:
    for col in DATE_CANDIDATES:
        if col in df.columns:
            return col
    return None


def attach_keys(
    df: pd.DataFrame,
    dataset_name: str,
    price_crosswalk: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, str]:
    if df.empty:
        raise ValueError(f"{dataset_name}: input file is empty.")
    date_col = find_date_column(df)
    if not date_col:
        raise ValueError(f"{dataset_name}: no recognised date field.")
    out = df.copy()
    out["_date_key"] = out[date_col].map(date_key)

    isin_col = find_isin_column(out)
    if isin_col:
        out["_isin_key"] = out[isin_col].astype(str).str.strip()
        out.loc[~out["_isin_key"].map(is_isin), "_isin_key"] = pd.NA
        return out, f"direct:{isin_col}"

    code_col = find_code_column(out)
    if code_col and price_crosswalk is not None and not price_crosswalk.empty:
        out["_code_key"] = out[code_col].astype(str).str.strip()
        out = out.merge(
            price_crosswalk,
            how="left",
            on=["_date_key", "_code_key"],
            validate="many_to_one",
        )
        return out, f"price_crosswalk:{code_col}"
    raise ValueError(f"{dataset_name}: no reliable ISIN field or date-specific code crosswalk.")


def make_price_crosswalk(prices: pd.DataFrame) -> pd.DataFrame | None:
    if prices.empty:
        return None
    date_col = find_date_column(prices)
    isin_col = find_isin_column(prices)
    code_col = find_code_column(prices)
    if not date_col or not isin_col or not code_col:
        return None
    cross = prices[[date_col, isin_col, code_col]].copy()
    cross["_date_key"] = cross[date_col].map(date_key)
    cross["_code_key"] = cross[code_col].astype(str).str.strip()
    cross["_isin_key"] = cross[isin_col].astype(str).str.strip()
    cross = cross[cross["_isin_key"].map(is_isin) & cross["_date_key"].notna()]
    cross = cross[["_date_key", "_code_key", "_isin_key"]].drop_duplicates()
    if cross.duplicated(["_date_key", "_code_key"]).any():
        raise ValueError("Price crosswalk has multiple ISINs for the same date/code; refusing ambiguous joins.")
    return cross


def prepare_dataset(
    df: pd.DataFrame,
    name: str,
    crosswalk: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, str]:
    keyed, method = attach_keys(df, name, crosswalk)
    keyed = keyed[keyed["_date_key"].notna() & keyed["_isin_key"].notna()].copy()
    if keyed.empty:
        raise ValueError(f"{name}: no rows with both a valid date and ISIN.")
    # Exact duplicate source rows are safe to remove; conflicting duplicates
    # for one date/ISIN are not aggregated or silently selected.
    keyed = keyed.drop_duplicates()
    if keyed.duplicated(["_date_key", "_isin_key"]).any():
        raise ValueError(f"{name}: multiple non-identical rows share the same date/ISIN key.")
    raw_cols = [
        c for c in keyed.columns
        if c not in {"_date_key", "_isin_key", "_code_key"}
    ]
    out = keyed[["_date_key", "_isin_key"] + raw_cols].copy()
    rename = {c: f"{name}__{c}" for c in raw_cols}
    out = out.rename(columns=rename)
    out[f"_{name}_present"] = True
    return out, method


def write_summary(text: str):
    SUMMARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_FILE.write_text(text, encoding="utf-8")


def main() -> int:
    lending = read_data(LENDING_FILE)
    volume = read_data(SHORT_VOLUME_FILE)
    balance = read_data(SHORT_BALANCE_FILE)
    prices = read_data(PRICE_FILE)

    if lending.empty:
        write_summary("비교파일 미생성: 대차 원자료가 없습니다.\n")
        print("[COMPARE] lending data missing; comparison not generated.", flush=True)
        return 0
    if volume.empty and balance.empty:
        write_summary("비교파일 미생성: 결합할 공매도 거래량/잔고 자료가 없습니다.\n")
        print("[COMPARE] short-selling data missing; comparison not generated.", flush=True)
        return 0

    crosswalk = make_price_crosswalk(prices)
    datasets = [("lending", lending)]
    if not volume.empty:
        datasets.append(("short_volume", volume))
    if not balance.empty:
        datasets.append(("short_balance", balance))
    if not prices.empty:
        datasets.append(("daily_prices", prices))

    prepared = []
    methods = []
    for name, frame in datasets:
        keyed, method = prepare_dataset(frame, name, crosswalk)
        prepared.append(keyed)
        methods.append(f"{name}={method}")

    merged = prepared[0]
    for frame in prepared[1:]:
        merged = merged.merge(
            frame,
            how="outer",
            on=["_date_key", "_isin_key"],
            validate="one_to_one",
        )

    present_cols = [c for c in merged.columns if c.endswith("_present")]
    for col in present_cols:
        merged[col] = merged[col].fillna(False).astype(bool)

    has_lending = merged.get("_lending_present", pd.Series(False, index=merged.index))
    has_volume = merged.get("_short_volume_present", pd.Series(False, index=merged.index))
    has_balance = merged.get("_short_balance_present", pd.Series(False, index=merged.index))
    has_short = has_volume | has_balance
    merged["match_status"] = "unmatched"
    merged.loc[has_lending & has_short, "match_status"] = "lending_and_shorting"
    merged.loc[has_lending & ~has_short, "match_status"] = "lending_only"
    merged.loc[~has_lending & has_short, "match_status"] = "shorting_only"
    merged = merged.rename(columns={"_date_key": "trade_date", "_isin_key": "isin"})
    merged = merged.sort_values(["trade_date", "isin"], kind="stable")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(OUTPUT_FILE, index=False, compression="gzip", encoding="utf-8")

    status_counts = merged["match_status"].value_counts().to_dict()
    summary = (
        "공매도·주식대차 비교파일 생성 결과\n"
        f"파일: {OUTPUT_FILE.relative_to(ROOT)}\n"
        f"행 수: {len(merged):,}\n"
        f"종목(ISIN) 수: {merged['isin'].nunique():,}\n"
        f"기준일 범위: {merged['trade_date'].min()} ~ {merged['trade_date'].max()}\n"
        f"결합 키: 기준일 + ISIN (종목명으로 결합하지 않음)\n"
        f"ISIN 확인 방법: {'; '.join(methods)}\n"
        f"대차+공매도 일치: {status_counts.get('lending_and_shorting', 0):,}\n"
        f"대차만 존재: {status_counts.get('lending_only', 0):,}\n"
        f"공매도만 존재: {status_counts.get('shorting_only', 0):,}\n"
        "주의: 대차잔고는 공매도 잔고가 아닙니다. 대차 후 공매도하지 않은 물량도 포함될 수 있습니다.\n"
        "주의: 원자료의 결측값은 보정하지 않았습니다. 거래일별 공백은 원자료의 수집상태와 함께 해석해야 합니다.\n"
        "이용조건: 금융위원회 주식대차정보는 공공누리 제2유형(출처표시, 상업적 이용금지)입니다.\n"
    )
    write_summary(summary)
    print(summary, flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        message = str(exc)
        write_summary(f"비교파일 미생성: {message}\n")
        print(f"[COMPARE][BLOCKED] {message}", flush=True)
        raise SystemExit(1)

#!/usr/bin/env python3
"""Analyze collected KRX short data, highlighting configured domestic holdings.

This script only reads repository data; it does not call KRX. It uses the Korean
symbols in config/bollinger_watchlist.json (quantity + average price marks an
explicit holding; entries without them are watchlist-only) and never emits a
buy/sell recommendation.
"""
from __future__ import annotations

import json
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
WATCHLIST_FILE = ROOT / "config" / "bollinger_watchlist.json"
SUMMARY_FILE = OUTPUT_DIR / "shorting_analysis_summary.txt"
TOP_CSV = OUTPUT_DIR / "shorting_analysis_top_symbols.csv"
HOLDINGS_CSV = OUTPUT_DIR / "shorting_analysis_holdings.csv"

DATE_COLUMNS = ["_query_date", "TRD_DD", "trdDd", "BAS_DT", "basDt", "DATE", "date"]
CODE_COLUMNS = ["ISU_SRT_CD", "isuSrtCd", "종목코드(단축)", "종목코드", "code", "ISU_CD", "isuCd"]
NAME_COLUMNS = ["ISU_ABBRV", "ISU_NM", "isuNm", "종목명", "name"]


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, compression="gzip", dtype=str, low_memory=False)


def to_number(series: pd.Series) -> pd.Series:
    """Parse numeric text without destroying negative signs."""
    if series.empty:
        return pd.Series([], dtype="float64")
    cleaned = (series.astype(str).str.replace(",", "", regex=False)
               .str.replace("%", "", regex=False).str.strip())
    cleaned = cleaned.replace({"": pd.NA, "-": pd.NA, "nan": pd.NA, "None": pd.NA})
    return pd.to_numeric(cleaned, errors="coerce")


def pick_column(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    return next((candidate for candidate in candidates if candidate in df.columns), None)


def normalize_date_series(series: pd.Series) -> pd.Series:
    out = series.astype(str).str.strip().str.replace(r"[^0-9]", "", regex=True)
    return out.where(out.str.len() >= 8, pd.NA).str[:8]


def normalized_name(value: object) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def normalize_symbol(value: object) -> str:
    text = str(value or "").strip().replace(".0", "")
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text.upper()


def load_korean_watchlist() -> list[dict]:
    if not WATCHLIST_FILE.exists():
        return []
    payload = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    items = []
    for item in payload.get("symbols", []):
        if str(item.get("market", "")).upper() != "KR":
            continue
        symbol = normalize_symbol(item.get("symbol"))
        if not re.fullmatch(r"\d{6}", symbol):
            continue  # Exclude indices and non-domestic ticker formats.
        qty = item.get("qty")
        avg = item.get("avg_price")
        items.append({
            "symbol": symbol,
            "name": str(item.get("name") or symbol).strip(),
            "qty": qty,
            "avg_price": avg,
            "owned": qty not in (None, "", 0, "0") and avg not in (None, "", 0, "0"),
        })
    return items


def date_values(df: pd.DataFrame) -> tuple[list[str], str | None, str | None]:
    if df.empty:
        return [], None, None
    col = pick_column(df, DATE_COLUMNS)
    if not col:
        return [], None, None
    dates = sorted(normalize_date_series(df[col]).dropna().unique().tolist())
    return dates, dates[0] if dates else None, dates[-1] if dates else None


def pick_code_col(df: pd.DataFrame) -> str | None:
    # Prefer the six-digit short code over ISIN, which is not the watchlist key.
    for col in CODE_COLUMNS:
        if col not in df.columns:
            continue
        sample = df[col].dropna().astype(str).str.strip().head(500)
        if len(sample) and sample.map(lambda x: bool(re.fullmatch(r"\d{6}(?:\.0)?", x))).mean() >= 0.5:
            return col
    return None


def top_symbols_for_day(df: pd.DataFrame, metric_col: str | None, code_col: str | None,
                        date_col: str | None, day: str | None) -> pd.DataFrame:
    result_cols = ["date", "code", "name", "metric"]
    if df.empty or not metric_col or not code_col or not date_col or not day:
        return pd.DataFrame(columns=result_cols)
    work = df.copy()
    work["_date"] = normalize_date_series(work[date_col])
    work = work[work["_date"] == day].copy()
    work["metric"] = to_number(work[metric_col])
    work = work[work["metric"].notna()].copy()
    if work.empty:
        return pd.DataFrame(columns=result_cols)
    name_col = pick_column(work, NAME_COLUMNS)
    work["name"] = work[name_col].astype(str) if name_col else work[code_col].astype(str)
    work["code"] = work[code_col].astype(str)
    return (work.assign(date=day)[["date", "code", "name", "metric"]]
            .sort_values("metric", ascending=False).head(10).reset_index(drop=True))


def symbol_rows(df: pd.DataFrame, holding: dict) -> tuple[pd.DataFrame, str | None]:
    """Find a holding by six-digit ticker, falling back to exact normalized name."""
    if df.empty:
        return pd.DataFrame(), None
    date_col = pick_column(df, DATE_COLUMNS)
    if not date_col:
        return pd.DataFrame(), None
    code_col = pick_code_col(df)
    mask = pd.Series(False, index=df.index)
    if code_col:
        mask |= df[code_col].map(normalize_symbol) == holding["symbol"]
    name_col = pick_column(df, NAME_COLUMNS)
    if name_col:
        target = normalized_name(holding["name"])
        mask |= df[name_col].map(normalized_name) == target
        aliases = {"naver": {"네이버"}, "sk하이닉스": {"skhynix", "skhynixinc"},
                   "한국콜마": {"한국콜마홀딩스"}}
        for alias in aliases.get(target, set()):
            mask |= df[name_col].map(normalized_name) == alias
    rows = df.loc[mask].copy()
    if not rows.empty:
        rows["_date"] = normalize_date_series(rows[date_col])
        rows = rows[rows["_date"].notna()].sort_values("_date")
    return rows, date_col


def latest_metric(df: pd.DataFrame, holding: dict, candidates: list[str]) -> tuple[float | None, str | None, float | None]:
    rows, _ = symbol_rows(df, holding)
    if rows.empty:
        return None, None, None
    metric_col = pick_column(rows, candidates)
    if not metric_col:
        return None, None, None
    rows["_value"] = to_number(rows[metric_col])
    rows = rows[rows["_value"].notna()].drop_duplicates("_date", keep="last")
    if rows.empty:
        return None, None, None
    latest = rows.iloc[-1]
    recent = rows.tail(5)
    change = None
    if len(recent) >= 2 and float(recent.iloc[0]["_value"]) != 0:
        change = (float(recent.iloc[-1]["_value"]) / float(recent.iloc[0]["_value"]) - 1) * 100
    return float(latest["_value"]), str(latest["_date"]), change


def analyze_holdings(holdings: list[dict], volume: pd.DataFrame,
                     balance: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    records = []
    for item in holdings:
        vol, vol_date, vol_change = latest_metric(
            volume, item, ["CVSRTSELL_TRDVOL", "ACC_TRDVOL", "total_volume"])
        bal, bal_date, bal_change = latest_metric(
            balance, item, ["BAL_QTY", "BAL_AMT", "BAL_RTO", "balance_qty"])
        price, price_date, price_change = latest_metric(
            prices, item, ["FLUC_RT", "TDD_CLSPRC", "close", "Close"])
        records.append({
            "symbol": item["symbol"], "name": item["name"],
            "status": "보유" if item["owned"] else "관심종목",
            "qty": item["qty"] if item["qty"] is not None else "",
            "avg_price": item["avg_price"] if item["avg_price"] is not None else "",
            "volume_date": vol_date or "", "short_volume": vol,
            "short_volume_5obs_pct": vol_change,
            "balance_date": bal_date or "", "short_balance": bal,
            "short_balance_5obs_pct": bal_change,
            "price_date": price_date or "", "price_metric": price,
            "price_5obs_pct": price_change,
        })
    return pd.DataFrame(records)


def fmt(value: object, decimals: int = 0) -> str:
    if value is None or pd.isna(value):
        return "자료 없음"
    return f"{float(value):,.{decimals}f}"


def main() -> int:
    volume, balance, prices = read_csv(VOLUME_FILE), read_csv(BALANCE_FILE), read_csv(PRICE_FILE)
    vd, vs, ve = date_values(volume)
    bd, bs, be = date_values(balance)
    pdays, ps, pe = date_values(prices)
    common = set(vd) & set(bd) & set(pdays)
    common_day = max(common) if common else None
    holdings = load_korean_watchlist()
    holdings_report = analyze_holdings(holdings, volume, balance, prices)

    v_top = top_symbols_for_day(volume, pick_column(volume, ["CVSRTSELL_TRDVOL", "ACC_TRDVOL", "total_volume"]),
                                pick_code_col(volume), pick_column(volume, DATE_COLUMNS), common_day)
    b_top = top_symbols_for_day(balance, pick_column(balance, ["BAL_QTY", "BAL_AMT", "BAL_RTO", "balance_qty"]),
                                pick_code_col(balance), pick_column(balance, DATE_COLUMNS), common_day)
    p_top = top_symbols_for_day(prices, pick_column(prices, ["FLUC_RT", "TDD_CLSPRC", "close", "Close"]),
                                pick_code_col(prices), pick_column(prices, DATE_COLUMNS), common_day)

    lines = [
        "KRX 수집 데이터 분석 결과", "========================", "",
        f"volume 파일: {len(vd)}개 날짜, {len(volume):,} rows, 범위 {vs} ~ {ve}",
        f"balance 파일: {len(bd)}개 날짜, {len(balance):,} rows, 범위 {bs} ~ {be}",
        f"price 파일: {len(pdays)}개 날짜, {len(prices):,} rows, 범위 {ps} ~ {pe}",
        f"공통 유효 날짜: {len(common)}개", f"최종 공통 일자: {common_day or '없음'}", "",
        "[보유·관심 국내 종목 중심 분석]",
        "보유 표시는 config/bollinger_watchlist.json에 수량과 평단가가 모두 입력된 종목입니다.",
        "5회 관측 변화는 최근 최대 5개 관측치의 첫 값 대비 최신 값이며, 투자 권고가 아닙니다.",
    ]
    if holdings_report.empty:
        lines.append("- 국내 종목 설정을 찾지 못했습니다.")
    else:
        for _, row in holdings_report.iterrows():
            if pd.isna(row["short_volume"]) and pd.isna(row["short_balance"]) and pd.isna(row["price_metric"]):
                continue
            lines.append(
                f"- {row['status']} {row['symbol']} {row['name']} | "
                f"공매도 거래량 {fmt(row['short_volume'])} ({row['volume_date'] or '날짜 없음'}, "
                f"5회 관측 변화 {fmt(row['short_volume_5obs_pct'], 1)}%) | "
                f"잔고 {fmt(row['short_balance'])} ({row['balance_date'] or '날짜 없음'}, "
                f"5회 관측 변화 {fmt(row['short_balance_5obs_pct'], 1)}%) | "
                f"가격지표 {fmt(row['price_metric'], 3)} ({row['price_date'] or '날짜 없음'})"
            )
        if not holdings_report.empty and not any(
            not (pd.isna(r.short_volume) and pd.isna(r.short_balance) and pd.isna(r.price_metric))
            for r in holdings_report.itertuples()
        ):
            lines.append("- 설정 종목과 일치하는 KRX 자료가 없습니다. 티커/종목명 매칭을 확인하세요.")

    lines.extend(["", "[최근 공통 기준일 상위 종목]"])
    for title, frame in [("공매도 거래량 상위 10", v_top), ("공매도 잔고 상위 10", b_top),
                         ("가격지표 상위 10 (등락률 부호 검증 전 참고용)", p_top)]:
        lines.extend(["", f"[{title}]"])
        if frame.empty:
            lines.append("- 자료 없음")
        else:
            for _, row in frame.iterrows():
                lines.append(f"- {row['code']} ({row['name']}): {fmt(row['metric'], 3 if '가격' in title else 0)}")

    lines.extend(["", "해석 주의: 최신 잔고는 KRX 보고 지연으로 거래량·가격보다 늦을 수 있습니다.",
                  "5회 관측 변화는 거래일 간 누적 비교가 아니라 수집된 관측 행 기준의 단순 변화입니다.",
                  "공매도 거래량·잔고는 단독으로 매수/매도 신호가 아니며, 가격지표 상위 순위는 부호/단위 검증 전 참고용입니다."])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not holdings_report.empty:
        holdings_report.to_csv(HOLDINGS_CSV, index=False, encoding="utf-8-sig")
    top_frames = []
    for label, frame in [("volume", v_top), ("balance", b_top), ("price", p_top)]:
        if not frame.empty:
            out = frame.copy()
            out["dataset"] = label
            top_frames.append(out)
    if top_frames:
        pd.concat(top_frames, ignore_index=True).to_csv(TOP_CSV, index=False, encoding="utf-8-sig")
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

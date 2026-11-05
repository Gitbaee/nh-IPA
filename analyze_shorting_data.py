#!/usr/bin/env python3
"""Summarize collected KRX short data, prioritizing configured domestic holdings.

Reads only committed data; it does not make KRX requests or provide trading advice.
Domestic holdings are sourced from config/dart_watchlist.json and supplemented
with ticker/quantity/cost-basis details in config/bollinger_watchlist.json.
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
DART_WATCHLIST = ROOT / "config" / "dart_watchlist.json"
BOLLINGER_WATCHLIST = ROOT / "config" / "bollinger_watchlist.json"
SUMMARY_FILE = OUTPUT_DIR / "shorting_analysis_summary.txt"
TOP_CSV = OUTPUT_DIR / "shorting_analysis_top_symbols.csv"
HOLDINGS_CSV = OUTPUT_DIR / "shorting_analysis_holdings.csv"

DATE_COLUMNS = ["_query_date", "TRD_DD", "trdDd", "BAS_DT", "basDt", "DATE", "date"]
CODE_COLUMNS = ["ISU_SRT_CD", "isuSrtCd", "종목코드(단축)", "종목코드", "code", "ISU_CD", "isuCd"]
NAME_COLUMNS = ["ISU_ABBRV", "ISU_NM", "isuNm", "종목명", "name"]
VOLUME_COLUMNS = ["CVSRTSELL_TRDVOL", "ACC_TRDVOL", "total_volume"]
BALANCE_COLUMNS = ["BAL_QTY", "balance_qty"]
CLOSE_COLUMNS = ["TDD_CLSPRC", "close", "Close", "closing_price"]


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, compression="gzip", dtype=str, low_memory=False)


def to_number(series: pd.Series) -> pd.Series:
    """Parse numeric text while preserving negative signs and missing values."""
    if series.empty:
        return pd.Series([], dtype="float64")
    text = series.astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False).str.strip()
    text = text.replace({"": pd.NA, "-": pd.NA, "nan": pd.NA, "None": pd.NA})
    return pd.to_numeric(text, errors="coerce")


def pick_column(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    return next((column for column in candidates if column in df.columns), None)


def normalize_date_series(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip().str.replace(r"[^0-9]", "", regex=True)
    return text.where(text.str.len() >= 8, pd.NA).str[:8]


def normalize_name(value: object) -> str:
    return "".join(char.lower() for char in str(value or "") if char.isalnum())


def normalize_symbol(value: object) -> str:
    text = str(value or "").strip().replace(".0", "")
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text.upper()


def load_holdings() -> list[dict]:
    """Load named domestic holdings and enrich with configured codes/position data."""
    by_symbol: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    if DART_WATCHLIST.exists():
        config = json.loads(DART_WATCHLIST.read_text(encoding="utf-8"))
        aliases = config.get("domestic_aliases", {})
        for name in config.get("domestic_holdings", []):
            record = {"symbol": "", "name": str(name).strip(), "qty": "", "avg_price": "", "owned": True}
            by_name[normalize_name(name)] = record
            for alias in aliases.get(name, []):
                by_name[normalize_name(alias)] = record

    if BOLLINGER_WATCHLIST.exists():
        config = json.loads(BOLLINGER_WATCHLIST.read_text(encoding="utf-8"))
        for item in config.get("symbols", []):
            if str(item.get("market", "")).upper() != "KR":
                continue
            symbol = normalize_symbol(item.get("symbol"))
            if not re.fullmatch(r"\d{6}", symbol):
                continue
            name = str(item.get("name") or symbol).strip()
            qty, avg = item.get("qty", ""), item.get("avg_price", "")
            record = {"symbol": symbol, "name": name, "qty": qty, "avg_price": avg,
                      "owned": qty not in (None, "", 0, "0") and avg not in (None, "", 0, "0")}
            existing = by_name.get(normalize_name(name))
            if existing:
                existing.update(record)
                by_symbol[symbol] = existing
            else:
                by_symbol[symbol] = record
                by_name[normalize_name(name)] = record

    # Keep one report row per holding; ticker-backed rows take precedence over names.
    unique = []
    seen = set()
    for record in [*by_symbol.values(), *by_name.values()]:
        key = record["symbol"] or normalize_name(record["name"])
        if key and key not in seen:
            seen.add(key)
            unique.append(record)
    return sorted(unique, key=lambda record: (not record["owned"], record["name"].lower()))


def date_values(df: pd.DataFrame) -> tuple[list[str], str | None, str | None]:
    if df.empty:
        return [], None, None
    col = pick_column(df, DATE_COLUMNS)
    if not col:
        return [], None, None
    dates = sorted(normalize_date_series(df[col]).dropna().unique().tolist())
    return dates, dates[0] if dates else None, dates[-1] if dates else None


def pick_code_col(df: pd.DataFrame) -> str | None:
    # Only use a column when its values are predominantly six-digit KRX tickers.
    for col in CODE_COLUMNS:
        if col not in df.columns:
            continue
        sample = df[col].dropna().astype(str).str.strip().head(500)
        if len(sample) and sample.map(lambda value: bool(re.fullmatch(r"\d{6}(?:\.0)?", value))).mean() >= 0.5:
            return col
    return None


def symbol_rows(df: pd.DataFrame, holding: dict) -> pd.DataFrame:
    if df.empty or not pick_column(df, DATE_COLUMNS):
        return pd.DataFrame()
    code_col = pick_code_col(df)
    if holding["symbol"] and code_col:
        by_code = df[ df[code_col].map(normalize_symbol) == holding["symbol"] ]
        if not by_code.empty:
            return by_code.copy()
    name_col = pick_column(df, NAME_COLUMNS)
    if not name_col:
        return pd.DataFrame()
    target = normalize_name(holding["name"])
    aliases = {
        "삼성전자우": {"삼성전자", "삼성전자우선주"},
        "naver": {"네이버"},
        "sk하이닉스": {"sk hynix", "sk hynix inc.", "sk하이닉스"},
    }
    accepted = {target, *(normalize_name(alias) for alias in aliases.get(holding["name"], set()))}
    return df[df[name_col].map(normalize_name).isin(accepted)].copy()


def latest_metric(df: pd.DataFrame, holding: dict, candidates: list[str]) -> tuple[float | None, str | None, float | None]:
    rows = symbol_rows(df, holding)
    date_col = pick_column(rows, DATE_COLUMNS)
    metric_col = pick_column(rows, candidates)
    if rows.empty or not date_col or not metric_col:
        return None, None, None
    rows["_date"] = normalize_date_series(rows[date_col])
    rows["_value"] = to_number(rows[metric_col])
    rows = rows[rows["_date"].notna() & rows["_value"].notna()].sort_values("_date")
    rows = rows.drop_duplicates("_date", keep="last")
    if rows.empty:
        return None, None, None
    latest = rows.iloc[-1]
    recent = rows.tail(5)
    change = None
    first_value = float(recent.iloc[0]["_value"])
    if len(recent) >= 2 and first_value != 0:
        change = (float(recent.iloc[-1]["_value"]) / first_value - 1) * 100
    return float(latest["_value"]), str(latest["_date"]), change


def top_symbols_for_day(df: pd.DataFrame, metric_candidates: list[str], day: str | None) -> pd.DataFrame:
    cols = ["date", "code", "name", "metric"]
    code_col, date_col = pick_code_col(df), pick_column(df, DATE_COLUMNS)
    metric_col = pick_column(df, metric_candidates)
    if df.empty or not code_col or not date_col or not metric_col or not day:
        return pd.DataFrame(columns=cols)
    work = df.copy()
    work["date"] = normalize_date_series(work[date_col])
    work = work[work["date"] == day].copy()
    work["metric"] = to_number(work[metric_col])
    work = work[work["metric"].notna()]
    name_col = pick_column(work, NAME_COLUMNS)
    work["name"] = work[name_col].astype(str) if name_col else work[code_col].astype(str)
    work["code"] = work[code_col].map(normalize_symbol)
    return work[cols].sort_values("metric", ascending=False).head(10).reset_index(drop=True)


def fmt(value: object, decimals: int = 0) -> str:
    return "자료 없음" if value is None or pd.isna(value) else f"{float(value):,.{decimals}f}"


def main() -> int:
    volume, balance, prices = read_csv(VOLUME_FILE), read_csv(BALANCE_FILE), read_csv(PRICE_FILE)
    vd, vs, ve = date_values(volume)
    bd, bs, be = date_values(balance)
    price_dates, ps, pe = date_values(prices)
    common = set(vd) & set(bd) & set(price_dates)
    common_day = max(common) if common else None
    holdings = load_holdings()

    records = []
    for holding in holdings:
        vol, vol_date, vol_change = latest_metric(volume, holding, VOLUME_COLUMNS)
        bal, bal_date, bal_change = latest_metric(balance, holding, BALANCE_COLUMNS)
        close, close_date, price_change = latest_metric(prices, holding, CLOSE_COLUMNS)
        records.append({
            "symbol": holding["symbol"], "name": holding["name"],
            "status": "보유" if holding["owned"] else "관심종목",
            "qty": holding["qty"], "avg_price": holding["avg_price"],
            "volume_date": vol_date or "", "short_volume": vol,
            "short_volume_5obs_pct": vol_change,
            "balance_date": bal_date or "", "short_balance": bal,
            "short_balance_5obs_pct": bal_change,
            "price_date": close_date or "", "closing_price": close,
            "price_5obs_pct": price_change,
        })
    holdings_df = pd.DataFrame(records)
    volume_top = top_symbols_for_day(volume, VOLUME_COLUMNS, common_day)
    balance_top = top_symbols_for_day(balance, BALANCE_COLUMNS, common_day)

    lines = [
        "KRX 보유종목 중심 분석", "=====================", "",
        f"volume: {len(vd)}개 날짜, {len(volume):,}행 ({vs} ~ {ve})",
        f"balance: {len(bd)}개 날짜, {len(balance):,}행 ({bs} ~ {be})",
        f"price: {len(price_dates)}개 날짜, {len(prices):,}행 ({ps} ~ {pe})",
        f"세 자료 공통 기준일: {common_day or '없음'}", "",
        "[보유 종목별 현황]",
        "보유 여부는 국내 DART 보유목록과 보유 수량/평단가가 설정된 국내 watchlist를 기준으로 표시합니다.",
        "최근 5개 관측치 변화율은 해당 지표의 첫 관측값과 최신값 비교이며, 매매 신호가 아닙니다.",
    ]
    if holdings_df.empty:
        lines.append("- 국내 보유종목 설정이 없습니다.")
    else:
        for row in holdings_df.itertuples(index=False):
            # Always list every configured domestic holding; missing is not zero.
            lines.append(
                f"- {row.status} {row.symbol or '코드 미확인'} {row.name} | "
                f"거래량 {fmt(row.short_volume)} ({row.volume_date or '날짜 없음'}, 5회 변화 {fmt(row.short_volume_5obs_pct, 1)}%) | "
                f"잔고 {fmt(row.short_balance)} ({row.balance_date or '날짜 없음'}, 5회 변화 {fmt(row.short_balance_5obs_pct, 1)}%) | "
                f"종가 {fmt(row.closing_price)} ({row.price_date or '날짜 없음'}, 5회 변화 {fmt(row.price_5obs_pct, 1)}%)"
            )

    lines.extend(["", f"[시장 전체 상위 종목 — 기준일 {common_day or '없음'}]"])
    for title, frame in [("공매도 거래량 상위 10", volume_top), ("공매도 잔고 상위 10", balance_top)]:
        lines.extend(["", f"{title}:"])
        if frame.empty:
            lines.append("- 자료 없음")
        else:
            for row in frame.itertuples(index=False):
                lines.append(f"- {row.code} ({row.name}): {fmt(row.metric)}")
    lines.extend([
        "", "주의: 자료 없음은 0이 아니라 원자료에서 해당 종목/날짜를 찾지 못했다는 뜻입니다.",
        "최신 잔고는 KRX 보고 지연으로 거래량·가격보다 늦을 수 있습니다.",
        "공매도 거래량·잔고와 가격 변화만으로 매수·매도 결론을 내릴 수 없습니다.",
    ])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    holdings_df.to_csv(HOLDINGS_CSV, index=False, encoding="utf-8-sig")
    tops = []
    for label, frame in [("volume", volume_top), ("balance", balance_top)]:
        if not frame.empty:
            output = frame.copy()
            output["dataset"] = label
            tops.append(output)
    if tops:
        pd.concat(tops, ignore_index=True).to_csv(TOP_CSV, index=False, encoding="utf-8-sig")
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

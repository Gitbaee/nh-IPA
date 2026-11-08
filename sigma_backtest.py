#!/usr/bin/env python3
"""Historical, holdings-focused conditional sigma-line outcome study.

For each configured domestic ticker, classify each close using a trailing
20-session mean and population standard deviation. For sigma-zone and crossing
events, measure subsequent 1/5/20-session returns from already-collected KRX
prices. This is historical conditional statistics, not a forecast guarantee or
trading recommendation. Missing observations are never filled with zero.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
PRICE_FILE = ROOT / "data" / "shorting" / "daily_prices.csv.gz"
WATCHLIST_FILE = ROOT / "config" / "bollinger_watchlist.json"
OUTPUT_DIR = ROOT / "output"
SUMMARY_FILE = OUTPUT_DIR / "sigma_backtest_summary.txt"
DETAIL_FILE = OUTPUT_DIR / "sigma_backtest_events.csv"
DATE_COLUMNS = ["_query_date", "TRD_DD", "trdDd", "BAS_DT", "basDt", "DATE", "date"]
CODE_COLUMNS = ["ISU_SRT_CD", "isuSrtCd", "종목코드(단축)", "종목코드", "ISU_CD", "isuCd", "code"]
PRICE_COLUMNS = ["TDD_CLSPRC", "stck_prpr", "close", "Close", "closing_price"]


def normalize_code(value: object) -> str:
    text = str(value or "").strip().replace(".0", "")
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text.upper()


def first_column(frame: pd.DataFrame, candidates: list[str]) -> str | None:
    return next((name for name in candidates if name in frame.columns), None)


def load_holdings() -> list[dict]:
    if not WATCHLIST_FILE.exists():
        return []
    config = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    holdings = []
    for item in config.get("symbols", []):
        if str(item.get("market", "")).upper() != "KR":
            continue
        symbol = normalize_code(item.get("symbol"))
        if re.fullmatch(r"\d{6}", symbol):
            holdings.append({"symbol": symbol, "name": str(item.get("name") or symbol),
                             "owned": item.get("qty") not in (None, "", 0, "0")
                             and item.get("avg_price") not in (None, "", 0, "0")})
    return holdings


def classify(z: float) -> str:
    if z >= 2:
        return "upper_2sigma"
    if z >= 1:
        return "upper_1sigma"
    if z <= -2:
        return "lower_2sigma"
    if z <= -1:
        return "lower_1sigma"
    return "within_1sigma"


def event_frame(frame: pd.DataFrame, holding: dict, date_col: str, code_col: str,
                price_col: str) -> pd.DataFrame:
    selected = frame[frame[code_col].map(normalize_code) == holding["symbol"]].copy()
    if selected.empty:
        return pd.DataFrame()
    selected["date"] = selected[date_col].astype(str).str.replace(r"[^0-9]", "", regex=True).str[:8]
    selected["close"] = pd.to_numeric(selected[price_col].astype(str).str.replace(",", "", regex=False), errors="coerce")
    selected = selected[selected["date"].str.len() == 8].dropna(subset=["close"])
    selected = selected.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if len(selected) < 21:
        return pd.DataFrame()
    selected["mean20"] = selected["close"].rolling(20, min_periods=20).mean()
    selected["std20"] = selected["close"].rolling(20, min_periods=20).std(ddof=0)
    selected["zscore"] = (selected["close"] - selected["mean20"]) / selected["std20"].replace(0, pd.NA)
    selected = selected.dropna(subset=["zscore"]).copy()
    selected["zone"] = selected["zscore"].map(classify)
    prev = selected["zscore"].shift(1)
    selected["event"] = "zone:" + selected["zone"]
    crossing = [
        (prev < 2) & (selected["zscore"] >= 2, "cross_up_2sigma"),
        (prev < 1) & (selected["zscore"] >= 1, "cross_up_1sigma"),
        (prev > -1) & (selected["zscore"] <= -1, "cross_down_1sigma"),
        (prev > -2) & (selected["zscore"] <= -2, "cross_down_2sigma"),
    ]
    for condition, label in crossing:
        selected.loc[condition, "event"] = label
    for horizon in (1, 5, 20):
        selected[f"forward_{horizon}d_pct"] = (selected["close"].shift(-horizon) / selected["close"] - 1) * 100
    selected["symbol"] = holding["symbol"]
    selected["name"] = holding["name"]
    selected["holding_status"] = "보유" if holding["owned"] else "관심"
    return selected[["date", "symbol", "name", "holding_status", "close", "mean20", "std20", "zscore", "event",
                     "forward_1d_pct", "forward_5d_pct", "forward_20d_pct"]]


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not PRICE_FILE.exists():
        SUMMARY_FILE.write_text("과거 시그마 검증 불가: KRX 일봉 자료 파일이 없습니다.\n", encoding="utf-8")
        return 0
    frame = pd.read_csv(PRICE_FILE, compression="gzip", dtype=str, low_memory=False)
    date_col, code_col, price_col = (first_column(frame, DATE_COLUMNS), first_column(frame, CODE_COLUMNS),
                                     first_column(frame, PRICE_COLUMNS))
    if not all((date_col, code_col, price_col)):
        SUMMARY_FILE.write_text("과거 시그마 검증 불가: 날짜/종목코드/종가 열을 신뢰성 있게 식별하지 못했습니다.\n", encoding="utf-8")
        return 0

    all_events = []
    for holding in load_holdings():
        result = event_frame(frame, holding, date_col, code_col, price_col)
        if not result.empty:
            all_events.append(result)
    if not all_events:
        SUMMARY_FILE.write_text("과거 시그마 검증 불가: 설정된 국내 보유·관심종목의 종가 이력을 매칭하지 못했습니다.\n", encoding="utf-8")
        pd.DataFrame(columns=["date", "symbol", "name", "holding_status", "close", "mean20", "std20", "zscore", "event",
                              "forward_1d_pct", "forward_5d_pct", "forward_20d_pct"]).to_csv(DETAIL_FILE, index=False, encoding="utf-8-sig")
        return 0

    events = pd.concat(all_events, ignore_index=True)
    events.to_csv(DETAIL_FILE, index=False, encoding="utf-8-sig")
    lines = ["과거 시그마 라인 이벤트 검증 (국내 설정종목)", "=" * 48,
             f"가격 자료: {events['date'].min()} ~ {events['date'].max()} | 종목 {events['symbol'].nunique()}개",
             "방법: 20거래일 이동평균 ±1σ/±2σ, 모집단 표준편차(ddof=0). 이벤트 종가부터 이후 1/5/20 관측일 수익률을 측정.",
             "주의: 이는 과거 조건부 성과이며 미래 수익을 보장하지 않습니다. 수수료·세금·슬리피지 미반영.\n",
             "| 구분 | 이벤트 | 표본 | 1일 평균 | 1일 상승 비율 | 5일 평균 | 5일 상승 비율 | 20일 평균 | 20일 상승 비율 |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    labels = ["cross_up_2sigma", "cross_up_1sigma", "cross_down_1sigma", "cross_down_2sigma",
              "zone:upper_2sigma", "zone:upper_1sigma", "zone:within_1sigma", "zone:lower_1sigma", "zone:lower_2sigma"]
    for status in ("보유", "관심"):
        subset = events[events["holding_status"] == status]
        for label in labels:
            sample = subset[subset["event"] == label]
            if sample.empty:
                continue
            parts = [status, label, str(len(sample))]
            for horizon in (1, 5, 20):
                values = pd.to_numeric(sample[f"forward_{horizon}d_pct"], errors="coerce").dropna()
                if values.empty:
                    parts.extend(["자료 없음", "자료 없음"])
                else:
                    parts.extend([f"{values.mean():+.2f}%", f"{(values > 0).mean()*100:.1f}%"])
            lines.append("| " + " | ".join(parts) + " |")
    lines.extend(["", "해석: 표본 수가 작으면 비율은 불안정합니다. 한 종목의 과거 빈도를 매매 신호로 단정하지 마세요.",
                  "이 결과는 가격 데이터만 사용합니다. 투자자별 수급은 매칭된 과거 시계열이 확보된 경우에만 별도 검증해야 합니다."])
    SUMMARY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

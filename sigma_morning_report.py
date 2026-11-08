#!/usr/bin/env python3
"""Send a per-symbol 20-session closing-price sigma report to Telegram.

This is a statistical price-position summary, not investor-class flow data.
The latest available close is used (market holidays and delayed feeds are respected).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from Alerts import emit_alert

ROOT = Path(__file__).resolve().parent
WATCHLIST = ROOT / "config" / "bollinger_watchlist.json"
OUTPUT = ROOT / "output"
KST = ZoneInfo("Asia/Seoul")
WINDOW = 20
STD_DDOF = 0


def yahoo_symbol(item: dict) -> str:
    symbol = str(item.get("symbol", "")).strip()
    market = str(item.get("market", "KR")).upper()
    if not symbol:
        return ""
    if symbol.startswith("^"):
        return symbol
    if market == "KR" and symbol.isdigit() and "." not in symbol:
        return f"{symbol}.KQ" if str(item.get("exchange", "")).upper() in {"KOSDAQ", "KQ"} else f"{symbol}.KS"
    if symbol == "BRK.B":
        return "BRK-B"
    return symbol


def calculate(item: dict) -> dict:
    ticker = yahoo_symbol(item)
    label = str(item.get("name") or ticker)
    try:
        frame = yf.download(ticker, period="3mo", interval="1d", progress=False,
                            auto_adjust=True, actions=False, threads=False)
        if frame is None or frame.empty or "Close" not in frame:
            raise ValueError("시세 자료 없음")
        close_series = frame["Close"]
        if isinstance(close_series, pd.DataFrame):
            close_series = close_series.iloc[:, 0]
        close = pd.to_numeric(close_series, errors="coerce").dropna()
        if len(close) < WINDOW:
            raise ValueError(f"종가 {len(close)}개로 20개 미달")
        sample = close.tail(WINDOW)
        mean = float(sample.mean())
        std = float(sample.std(ddof=STD_DDOF))
        latest = float(sample.iloc[-1])
        if not all(math.isfinite(x) for x in (mean, std, latest)) or std <= 0:
            raise ValueError("표준편차 계산 불가")
        z = (latest - mean) / std
        if z >= 2:
            state = "+2σ 이상"
        elif z >= 1:
            state = "+1σ~+2σ"
        elif z <= -2:
            state = "−2σ 이하"
        elif z <= -1:
            state = "−1σ~−2σ"
        else:
            state = "중심선 ±1σ"
        latest_date = close.index[-1]
        date_text = latest_date.strftime("%Y-%m-%d") if hasattr(latest_date, "strftime") else str(latest_date)
        return {"label": label, "ticker": ticker, "date": date_text, "close": latest,
                "z": z, "state": state, "mean": mean, "std": std, "error": ""}
    except Exception as exc:
        return {"label": label, "ticker": ticker, "date": "", "close": None,
                "z": None, "state": "계산 불가", "mean": None, "std": None,
                "error": str(exc)[:100]}


def main() -> int:
    cfg = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    items = cfg.get("symbols", [])
    results = [calculate(item) for item in items]
    stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [f"기준: 종목별 최근 20거래일 종가 | SMA20 ± σ | 모집단 표준편차(ddof=0)",
             f"생성: {stamp}", "※ 시그마는 가격 위치 통계이며 개인·기관·외국인 순매수 데이터가 아닙니다.", ""]
    for r in results:
        if r["z"] is None:
            lines.append(f"• {r['label']} ({r['ticker']}) | σ: 계산 불가 | {r['error'] or '자료 없음'}")
        else:
            lines.append(f"• {r['label']} ({r['ticker']}) | {r['date']} 종가 {r['close']:,.2f} | "
                         f"σ {r['z']:+.2f} | {r['state']}")
    report = "\n".join(lines)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "sigma_morning_report.txt").write_text(report + "\n", encoding="utf-8")
    # Telegram's message limit is 4096 chars. Keep chunks well below it.
    chunks, current = [], ""
    for line in lines:
        if len(current) + len(line) + 1 > 3500:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    for i, chunk in enumerate(chunks, 1):
        suffix = f" ({i}/{len(chunks)})" if len(chunks) > 1 else ""
        emit_alert(f"종목별 20일 시그마 업데이트{suffix}", chunk)
    print(report, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

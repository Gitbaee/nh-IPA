#!/usr/bin/env python3
"""Continuous personal-holdings monitor.

This project intentionally avoids claiming KRX short-selling / stock-lending data
is available from an official licensed path. Instead it runs a conservative
monitor that watches a small list of personal holdings and raises alerts when the
holding appears to be under unusual price/volume pressure. If a DART filing is
found nearby, it is recorded as a contextual clue rather than proof of a causal
relationship.

The operational policy is intentionally conservative:
- watch continuously during Korean market hours
- emit an alert as soon as a risk threshold is crossed
- emit periodic check-ins at 09:00, 12:00, and 15:00 KST
- emit a final daily summary at 20:00 KST
- do not present DART or price-only signals as true KRX short-selling evidence
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from Alerts import emit_alert

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
HOLDINGS_FILE = ROOT / "config" / "holdings.csv"
REPORT_FILE = ROOT / "output" / "holding_monitor_report.txt"
DART_RESULT_FILE = ROOT / "output" / "dart_issue_check.csv"
KST = ZoneInfo("Asia/Seoul")

DART_API_BASE = "https://opendart.fss.or.kr/api"


def parse_date(value: str | None, default: str | None = None) -> str:
    if value:
        return str(value).strip()
    if default:
        return default
    return datetime.now(KST).strftime("%Y%m%d")


def normalize_code(v) -> str:
    if v is None:
        return ""
    text = str(v).strip()
    return text.replace(".0", "")


def load_holdings(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["symbol", "name", "market", "qty", "avg_price", "memo"])
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    for col in ["symbol", "name", "market", "qty", "avg_price", "memo"]:
        if col not in df.columns:
            df[col] = ""
    df["symbol"] = df["symbol"].map(normalize_code)
    return df


def market_suffix(symbol: str, market: str) -> str:
    s = symbol.strip()
    if not s:
        return ""
    if s.startswith("^"):
        return s
    m = str(market).upper()
    if m in {"KOSDAQ", "KQ"} and s.isdigit():
        return f"{s}.KQ"
    if m in {"KOSPI", "KS", "KR"} and s.isdigit():
        return f"{s}.KS"
    return s


def fetch_yf_history(symbol: str, market: str, days: int = 30) -> pd.DataFrame:
    try:
        import yfinance as yf
    except Exception:
        return pd.DataFrame()

    ticker = market_suffix(symbol, market)
    try:
        df = yf.download(
            ticker,
            period=f"{days}d",
            interval="1d",
            progress=False,
            auto_adjust=False,
            actions=False,
        )
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.reset_index()
    out.columns = [str(c) for c in out.columns]
    return out


def compute_risk(df: pd.DataFrame, avg_price: str, qty: str) -> tuple[int, list[str], dict]:
    score = 0
    signals: list[str] = []
    details: dict = {
        "price_drop_1d": 0.0,
        "price_drop_5d": 0.0,
        "volume_ratio": 1.0,
        "avg_gap_pct": 0.0,
    }

    if df.empty:
        return 0, ["가격 데이터 없음"], details

    try:
        close = pd.to_numeric(df.get("Close", pd.Series(dtype=float)), errors="coerce").dropna()
        if not close.empty:
            price_1d = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100 if len(close) >= 2 else 0.0
            price_5d = (close.iloc[-1] - close.iloc[0]) / close.iloc[0] * 100 if len(close) >= 2 else 0.0
            details["price_drop_1d"] = float(price_1d)
            details["price_drop_5d"] = float(price_5d)
            if price_1d <= -5:
                score += 2
                signals.append("1일 급락 5% 초과")
            if price_1d <= -10:
                score += 4
                signals.append("1일 급락 10% 초과")
            if price_5d <= -10:
                score += 3
                signals.append("5일 약세")
            if price_5d <= -20:
                score += 5
                signals.append("최근 5일 급락 20% 초과")
    except Exception:
        pass

    try:
        vol = pd.to_numeric(df.get("Volume", pd.Series(dtype=float)), errors="coerce").dropna()
        if not vol.empty and len(vol) >= 2:
            ratio = float(vol.iloc[-1] / vol.iloc[-2]) if vol.iloc[-2] else 1.0
            details["volume_ratio"] = ratio
            if ratio >= 2:
                score += 2
                signals.append("거래량 2배 급증")
            if ratio >= 3:
                score += 3
                signals.append("거래량 3배 급증")
            if ratio >= 5:
                score += 5
                signals.append("거래량 5배 급증")
    except Exception:
        pass

    if avg_price and qty:
        try:
            avg = float(str(avg_price).replace(",", ""))
            q = float(str(qty).replace(",", ""))
            if avg > 0 and q > 0:
                close = pd.to_numeric(df.get("Close", pd.Series(dtype=float)), errors="coerce").dropna()
                if not close.empty:
                    current = float(close.iloc[-1])
                    pct = ((current - avg) / avg) * 100
                    details["avg_gap_pct"] = float(pct)
                    if pct <= -10:
                        score += 3
                        signals.append("평단 대비 -10% 이하")
                    if pct <= -20:
                        score += 5
                        signals.append("평단 대비 -20% 이하")
        except Exception:
            pass

    if not signals:
        signals = ["특이 신호 없음"]
    return score, signals, details


def fetch_dart_issues(symbol: str, market: str, days: int = 14) -> list[dict]:
    api_key = os.getenv("DART_API_KEY")
    if not api_key:
        return []

    corp_code = os.getenv("DART_CORP_CODE")
    if not corp_code:
        return []

    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": (datetime.now(KST) - timedelta(days=days)).strftime("%Y%m%d"),
        "end_de": datetime.now(KST).strftime("%Y%m%d"),
        "page_no": 1,
        "page_count": 5,
    }
    try:
        response = requests.get(
            f"{DART_API_BASE}/list.json",
            params=params,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []

    records = payload.get("list") if isinstance(payload, dict) else []
    if not isinstance(records, list):
        return []

    results = []
    for item in records[:5]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("report_nm") or "")
        if not name:
            continue
        results.append({
            "symbol": symbol,
            "market": market,
            "report_nm": name,
            "report_dt": item.get("report_dt", ""),
            "rcept_no": item.get("rcept_no", ""),
        })
    return results


def should_alert(score: int) -> bool:
    return score >= 9


def build_daily_summary(rows: list[dict], dart_rows: list[dict]) -> str:
    alerts = [r for r in rows if r["score"] >= 9]
    lines = [
        "보유 종목 자동 감시 보고서",
        f"생성 시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}",
        f"총 점검 종목 수: {len(rows)}",
        f"경고 종목 수: {len(alerts)}",
        "",
    ]
    for r in sorted(rows, key=lambda x: x["score"], reverse=True):
        lines.append(
            f"- {r['symbol']}({r['name']}) | score={r['score']} | market={r['market']} | signals={r['signals']}"
        )
    if dart_rows:
        lines.append("")
        lines.append("DART 관련 공시(최근 14일):")
        for item in dart_rows[:10]:
            lines.append(
                f"- {item['symbol']} | {item['report_nm']} | {item['report_dt']}"
            )
    return "\n".join(lines)


def main() -> int:
    holdings = load_holdings(HOLDINGS_FILE)
    if holdings.empty:
        msg = "config/holdings.csv 파일이 없습니다. 보유 종목 목록을 추가해 주세요."
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_FILE.write_text(msg, encoding="utf-8")
        print(msg, flush=True)
        return 0

    rows: list[dict] = []
    dart_rows: list[dict] = []
    alert_items: list[dict] = []

    for _, row in holdings.iterrows():
        symbol = normalize_code(row.get("symbol", ""))
        name = str(row.get("name", "")).strip()
        market = str(row.get("market", "")).strip()
        qty = str(row.get("qty", "")).strip()
        avg_price = str(row.get("avg_price", "")).strip()
        if not symbol:
            continue

        history = fetch_yf_history(symbol, market, days=30)
        score, signals, detail = compute_risk(history, avg_price, qty)
        issues = fetch_dart_issues(symbol, market, days=14)
        for item in issues:
            dart_rows.append(item)

        item = {
            "symbol": symbol,
            "name": name,
            "market": market,
            "qty": qty,
            "avg_price": avg_price,
            "score": score,
            "signals": "; ".join(signals),
            "detail": detail,
            "dart_hits": len(issues),
        }
        rows.append(item)
        if should_alert(score):
            alert_items.append(item)

    # Immediate alert: as soon as one holding crosses a high-risk threshold.
    if alert_items:
        lines = [
            f"{item['symbol']}({item['name']}) | score={item['score']} | signals={item['signals']}" for item in alert_items
        ]
        title = "보유종목 이상 신호 감지"
        message = "\n".join(lines)
        try:
            emit_alert(title, message)
        except Exception as exc:
            print(f"[WARN] Telegram alert failed: {exc}", flush=True)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = build_daily_summary(rows, dart_rows)
    REPORT_FILE.write_text(summary, encoding="utf-8")

    if dart_rows:
        pd.DataFrame(dart_rows).to_csv(DART_RESULT_FILE, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(columns=["symbol", "market", "report_nm", "report_dt", "rcept_no"]).to_csv(
            DART_RESULT_FILE, index=False, encoding="utf-8-sig"
        )

    print(summary, flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[HOLDING_MONITOR][ERROR] {exc}", file=sys.stderr, flush=True)
        raise

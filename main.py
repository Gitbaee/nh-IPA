#!/usr/bin/env python3
"""
main.py - market monitor

- robust numeric parsing
- safe time parsing for "15:20:00", "152000", etc.
- index handling for ^KS11, ^KQ11
- safe NaN/Volume checks
- brokerage avg_price/qty update support
- optional concurrent realtime tick fetching
"""
from __future__ import annotations

import os
import json
import math
import argparse
import logging
import traceback
from datetime import time, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

try:
    from auth import get_token
except Exception:
    def get_token() -> str:
        return "DUMMY"

try:
    from market_data import fetch_realtime_tick, fetch_overseas_balance
except Exception:
    def fetch_realtime_tick(code: str) -> List[Dict[str, Any]]:
        return []

    def fetch_overseas_balance() -> Dict[str, Any]:
        return {"rt_cd": "0", "holdings": []}


def parse_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if s == "":
        return None
    s = s.replace(",", "")
    allowed = set("0123456789.-")
    cleaned = "".join(ch for ch in s if ch in allowed)
    if cleaned in ("", "-", "."):
        return None
    try:
        return float(cleaned)
    except Exception:
        return None


def parse_hhmmss(raw: Any) -> Optional[time]:
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(s, fmt).time()
        except Exception:
            pass

    digits = "".join(ch for ch in s if ch.isdigit())
    try:
        if len(digits) == 6:
            return time(int(digits[0:2]), int(digits[2:4]), int(digits[4:6]))
        if len(digits) == 4:
            return time(int(digits[0:2]), int(digits[2:4]), 0)
        if len(digits) == 2:
            return time(int(digits[0:2]), 0, 0)
    except Exception:
        return None
    return None


def time_ge(t1: Optional[time], t2: time) -> bool:
    if t1 is None:
        return False
    return (t1.hour, t1.minute, t1.second) >= (t2.hour, t2.minute, t2.second)


def quant_filter(code: str, ticks: List[Dict[str, Any]]) -> List[str]:
    alerts: List[str] = []
    for t in ticks[-30:]:
        try:
            vol_raw = parse_number(t.get("cntg_vol", 0)) or 0.0
            price_raw = parse_number(t.get("stck_prpr", 0)) or 0.0
            vol = int(vol_raw)
            price = float(price_raw)
        except Exception:
            continue

        amount = vol * price
        raw_hour = str(t.get("bsop_hour", "") or "")
        hh = parse_hhmmss(raw_hour)

        if vol >= 20_000 or amount >= 100_000_000:
            alerts.append(f"[{code}] {raw_hour} {vol:,}주 {amount/1e8:.1f}억")

        if hh and time_ge(hh, time(15, 20, 0)):
            alerts.append(f"[{code}] 마감예상 {price:.0f}")
    return alerts


def load_watchlist(path: str = "config/watchlist.txt", limit: int = 100) -> List[str]:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            codes = [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith(("{", "["))
            ]
            if codes:
                return codes[:limit]
    return ["005930", "000660", "005380"]


def update_avg_from_brokerage(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    해외잔고 API를 통해 avg_price, qty를 자동 반영.
    예시 holdings:
      {"ovrs_pdno": "NVDA", "pchs_avg_pric": "235.2", "ovrs_cblc_qty": "10"}
    """
    try:
        overseas = fetch_overseas_balance()
        if not isinstance(overseas, dict):
            return cfg
        if overseas.get("rt_cd") == "0" and overseas.get("holdings"):
            holdings = overseas.get("holdings", [])
            for h in holdings:
                sym = h.get("ovrs_pdno") or h.get("symbol") or h.get("pdno")
                if not sym:
                    continue
                avg_raw = h.get("pchs_avg_pric") or h.get("avg_price") or h.get("pchs_avg_price")
                qty_raw = h.get("ovrs_cblc_qty") or h.get("qty") or h.get("holding_qty")

                avg_val = parse_number(avg_raw)
                qty_val = int(parse_number(qty_raw) or 0)

                for s in cfg.get("symbols", []):
                    if s.get("symbol") == sym:
                        if avg_val is not None:
                            s["avg_price"] = float(avg_val)
                        if qty_val:
                            s["qty"] = qty_val
        return cfg
    except Exception:
        logging.exception("update_avg_from_brokerage failed")
        return cfg


def run_bollinger_with_avg(cfg_path: str = "config/bollinger_watchlist.json", save_updates: bool = False) -> List[str]:
    try:
        import yfinance as yf
    except Exception:
        return ["yfinance 패키지가 없습니다. 설치하세요: pip install yfinance"]

    alerts: List[str] = []
    if not os.path.exists(cfg_path):
        return [f"{os.path.basename(cfg_path)} 없음"]

    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)

    cfg = update_avg_from_brokerage(cfg)

    if save_updates:
        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            logging.info("Saved brokerage-updated config to %s", cfg_path)
        except Exception:
            logging.exception("Failed to save updated config to %s", cfg_path)

    for s in cfg.get("symbols", [])[:100]:
        sym = s.get("symbol")
        name = s.get("name", sym)
        avg = parse_number(s.get("avg_price"))
        if sym is None:
            continue

        try:
            ysym = sym
            m = s.get("market", "KR")

            if str(sym).startswith("^"):
                ysym = sym
            elif m == "INDEX" and s.get("yahoo"):
                ysym = s.get("yahoo")
            elif str(m).upper() == "KR":
                if "." not in str(sym) and not str(sym).isalpha():
                    ysym = f"{sym}.KS"
                elif "." not in str(sym) and str(sym).isalpha() and len(str(sym)) <= 5:
                    pass
                elif "." not in str(sym):
                    ysym = f"{sym}.KS"

            if str(sym) == "BRK.B":
                ysym = "BRK-B"

            df = yf.download(ysym, period="1mo", interval="1d", progress=False, auto_adjust=True)
            if df is None or len(df) < 20:
                continue

            if "Close" not in df.columns:
                continue

            price = float(df["Close"].iloc[-1])
            if not math.isfinite(price):
                continue

            ma = float(df["Close"].rolling(20).mean().iloc[-1])
            std = float(df["Close"].rolling(20).std().iloc[-1])
            lower = ma - 2 * std
            upper = ma + 2 * std

            if "Volume" in df.columns:
                vol_today = float(df["Volume"].iloc[-1])
                vol_avg = float(df["Volume"].rolling(20).mean().iloc[-1])
                vol_ratio = vol_today / vol_avg if vol_avg > 0 else 0.0
            else:
                vol_today = 0.0
                vol_avg = 0.0
                vol_ratio = 0.0

            if price <= lower * 1.02:
                alerts.append(f"🔵 [{name} {sym}] 볼린저 하단 터치 {price:.0f} (하단 {lower:.0f})")
            if price >= upper * 0.98:
                alerts.append(f"🔴 [{name} {sym}] 볼린저 상단 터치 {price:.0f} (상단 {upper:.0f})")

            if vol_ratio >= 2.0:
                alerts.append(
                    f"💥 [{name} {sym}] 수급량 폭증! 거래량 {vol_ratio:.1f}배 {vol_today:,.0f}주 "
                    f"(평균 {vol_avg:,.0f}) 현재가 {price:.0f}"
                )
            elif vol_ratio >= 1.5:
                alerts.append(
                    f"📊 [{name} {sym}] 수급량 증가 {vol_ratio:.1f}배 {vol_today:,.0f}주 현재가 {price:.0f}"
                )

            if avg is not None and avg != 0:
                pct = (price - avg) / avg * 100
                if abs(pct) >= 5:
                    emo = "📈" if pct > 0 else "📉"
                    alerts.append(
                        f"{emo} [{name}] 평단 {avg} 대비 {pct:+.2f}% -> 현재 {price:.0f} ({s.get('qty', '')}주)"
                    )
                elif abs(pct) >= 4.5:
                    alerts.append(f"⚠️ [{name}] -5% 직전 {pct:+.2f}% 현재 {price:.0f}")

        except Exception:
            logging.exception("Error processing symbol %s", sym)
            alerts.append(f"[{sym}] 에러: {traceback.format_exc().splitlines()[-1]}")

    return alerts


def main() -> int:
    pa = argparse.ArgumentParser()
    pa.add_argument("--bollinger", action="store_true")
    pa.add_argument("--minutes", type=int, default=10)
    pa.add_argument("--markets", type=str, default="KR,US")
    pa.add_argument("--watchlist", type=str, default="config/watchlist.txt")
    pa.add_argument("--bollinger-cfg", type=str, default="config/bollinger_watchlist.json")
    pa.add_argument("--save-brokerage-updates", action="store_true")
    pa.add_argument("--concurrency", type=int, default=6)
    pa.add_argument("--log", type=str, default="")
    args = pa.parse_args()

    handlers = [logging.StreamHandler()]
    if args.log:
        handlers.append(logging.FileHandler(args.log, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(asctime)s %(levelname)s %(message)s")

    logging.info("TOKEN CHECK")
    try:
        get_token()
        logging.info("TOKEN OK")
    except Exception:
        logging.warning("token check failed; continuing in degraded mode")

    if args.bollinger:
        alerts = run_bollinger_with_avg(
            cfg_path=args.bollinger_cfg,
            save_updates=args.save_brokerage_updates
        )
        if not alerts:
            alerts = ["정상 스캔 - 알람 없음"]
    else:
        codes = load_watchlist(path=args.watchlist)
        logging.info("WATCHLIST %s", codes)

        results: List[str] = []
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
            future_to_code = {ex.submit(fetch_realtime_tick, c): c for c in codes}
            for fut in as_completed(future_to_code):
                c = future_to_code[fut]
                try:
                    ticks = fut.result(timeout=30)
                    results.extend(quant_filter(c, ticks))
                except Exception:
                    logging.exception("fetch/parse failed for %s", c)
                    results.append(f"[{c}] fetch/parse error")

        alerts = results if results else [f"정상 스캔 {len(codes)}종목 특이체결 없음"]

    os.makedirs("output", exist_ok=True)
    with open("output/summary.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(alerts))

    print("\n".join(alerts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

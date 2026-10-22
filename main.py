#!/usr/bin/env python3
"""
NH IPA 모니터 진입점
  python main.py --bollinger   → 볼린저 밴드 감시
  python main.py --foreign     → 외국계 순매수 실시간 감시
  python main.py               → 둘 다 실행
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

from alerts import emit_alert
from portfolio import load_watchlist


BOLLINGER_PATH = Path("config/bollinger_watchlist.json")
SUMMARY_PATH = Path("output/summary.txt")


def load_bollinger_config() -> dict:
    if not BOLLINGER_PATH.exists():
        return {"symbols": [], "settings": {"period": 20, "std": 2}}
    return json.loads(BOLLINGER_PATH.read_text(encoding="utf-8"))


def to_yahoo_symbol(item: dict) -> str | None:
    """종목 정보를 yfinance 심볼로 변환"""
    market = (item.get("market") or "").upper()
    symbol = str(item.get("symbol") or "").strip()
    yahoo = item.get("yahoo")

    if yahoo:
        return yahoo
    if not symbol:
        return None

    if market == "KR":
        # 코스피/코스닥 구분 없이 .KS 시도 (실패하면 .KQ)
        return f"{symbol}.KS"
    if market == "US":
        return symbol
    if market == "INDEX":
        return symbol  # 이미 ^KS11 형태
    return symbol


def calc_bollinger(df: pd.DataFrame, period: int = 20, std_mult: float = 2.0):
    close = df["Close"]
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return mid.iloc[-1], upper.iloc[-1], lower.iloc[-1], close.iloc[-1]


def run_bollinger() -> list[str]:
    """볼린저 밴드 감시 실행 → 알림 메시지 리스트 반환"""
    cfg = load_bollinger_config()
    symbols = cfg.get("symbols") or []
    settings = cfg.get("settings") or {}
    period = int(settings.get("period", 20))
    std_mult = float(settings.get("std", 2))

    alerts: list[str] = []
    summary_lines = [
        f"NH IPA 볼린저 감시 결과",
        f"생성시각: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"기간={period} / 표준편차={std_mult}",
        "",
    ]

    print(f"[볼린저] {len(symbols)}개 종목 검사 시작")

    for item in symbols:
        name = item.get("name") or item.get("symbol")
        ysym = to_yahoo_symbol(item)
        if not ysym:
            continue

        try:
            ticker = yf.Ticker(ysym)
            hist = ticker.history(period="3mo", interval="1d")
            if hist is None or len(hist) < period + 2:
                # 한국주식 .KS 실패 시 .KQ 재시도
                if ysym.endswith(".KS"):
                    ysym2 = ysym.replace(".KS", ".KQ")
                    hist = yf.Ticker(ysym2).history(period="3mo", interval="1d")
                    if hist is None or len(hist) < period + 2:
                        print(f"  [스킵] {name}({ysym}) 데이터 부족")
                        continue
                else:
                    print(f"  [스킵] {name}({ysym}) 데이터 부족")
                    continue

            mid, upper, lower, price = calc_bollinger(hist, period, std_mult)
            if pd.isna(mid) or pd.isna(upper) or pd.isna(lower):
                continue

            pos = "중간"
            signal = None
            if price <= lower:
                pos = "하단 이탈 (매수 관심)"
                signal = "LOWER"
            elif price >= upper:
                pos = "상단 이탈 (과열/매도 관심)"
                signal = "UPPER"

            line = (
                f"{name}({item.get('symbol')}) "
                f"종가={price:,.0f} "
                f"상단={upper:,.0f} 중간={mid:,.0f} 하단={lower:,.0f} → {pos}"
            )
            print(f"  {line}")
            summary_lines.append(line)

            if signal:
                body = (
                    f"종목: {name}\n"
                    f"코드: {item.get('symbol')}\n"
                    f"현재가: {price:,.0f}\n"
                    f"상단밴드: {upper:,.0f}\n"
                    f"중간밴드: {mid:,.0f}\n"
                    f"하단밴드: {lower:,.0f}\n"
                    f"상태: {pos}\n"
                    f"시각: {datetime.now():%Y-%m-%d %H:%M:%S}"
                )
                title = f"볼린저 {'하단' if signal == 'LOWER' else '상단'} 신호"
                emit_alert(title, body)
                alerts.append(f"{title} - {name}")

        except Exception as e:
            print(f"  [오류] {name}: {type(e).__name__}: {e}")

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text("\n".join(summary_lines), encoding="utf-8")
    print(f"[볼린저] 요약 저장 → {SUMMARY_PATH}")
    return alerts


async def run_foreign(seconds: int = 600):
    """외국계 순매수 실시간 감시"""
    from monitor import monitor

    codes = load_watchlist()
    if not codes:
        # 기본 워치리스트
        codes = ["005930", "000660", "005380"]
        print(f"[외국계] watchlist 비어있음 → 기본값 사용: {codes}")

    await monitor(codes, seconds=seconds)


def main():
    parser = argparse.ArgumentParser(description="NH IPA 모니터")
    parser.add_argument("--bollinger", action="store_true", help="볼린저 밴드만 실행")
    parser.add_argument("--foreign", action="store_true", help="외국계 순매수만 실행")
    parser.add_argument("--seconds", type=int, default=600, help="외국계 감시 시간(초)")
    args = parser.parse_args()

    # 둘 다 안 주면 둘 다 실행
    do_bollinger = args.bollinger or (not args.bollinger and not args.foreign)
    do_foreign = args.foreign or (not args.bollinger and not args.foreign)

    if do_bollinger:
        print("=" * 40)
        print("▶ 볼린저 밴드 감시 시작")
        print("=" * 40)
        run_bollinger()

    if do_foreign:
        print("=" * 40)
        print("▶ 외국계 순매수 실시간 감시 시작")
        print("=" * 40)
        asyncio.run(run_foreign(seconds=args.seconds))


if __name__ == "__main__":
    main()

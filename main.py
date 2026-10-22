#!/usr/bin/env python3
"""볼린저 밴드 감시 + 텔레그램 알림 (독립 실행)"""

import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

BOLLINGER_PATH = Path("config/bollinger_watchlist.json")
SUMMARY_PATH = Path("output/summary.txt")


def emit_alert(title: str, body: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

    print("\n" + "=" * 50)
    print(f"[ALERT] {title}")
    print(body)
    print("=" * 50 + "\n")

    if not token or not chat_id:
        print("[알림] 텔레그램 설정 없음 → 콘솔만 출력")
        return

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": f"🔔 {title}\n\n{body}"},
            timeout=10,
        )
        print("[알림] 텔레그램 전송", "성공" if resp.ok else f"실패 {resp.status_code}")
    except Exception as e:
        print(f"[알림] 예외: {e}")


def to_yahoo(item: dict) -> str | None:
    market = (item.get("market") or "").upper()
    symbol = str(item.get("symbol") or "").strip()
    if item.get("yahoo"):
        return item["yahoo"]
    if not symbol:
        return None
    if market == "KR":
        return f"{symbol}.KS"
    if market in ("US", "INDEX"):
        return symbol
    return symbol


def run():
    if not BOLLINGER_PATH.exists():
        print("config/bollinger_watchlist.json 없음")
        return

    cfg = json.loads(BOLLINGER_PATH.read_text(encoding="utf-8"))
    symbols = cfg.get("symbols") or []
    period = int(cfg.get("settings", {}).get("period", 20))
    std_mult = float(cfg.get("settings", {}).get("std", 2))

    print(f"[볼린저] {len(symbols)}개 종목 검사 (period={period}, std={std_mult})")
    lines = [
        f"NH 볼린저 감시 {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"period={period} std={std_mult}",
        "",
    ]

    for item in symbols:
        name = item.get("name") or item.get("symbol")
        ysym = to_yahoo(item)
        if not ysym:
            continue

        try:
            hist = yf.Ticker(ysym).history(period="3mo", interval="1d")
            if hist is None or len(hist) < period + 2:
                if ysym.endswith(".KS"):
                    hist = yf.Ticker(ysym.replace(".KS", ".KQ")).history(period="3mo", interval="1d")
                if hist is None or len(hist) < period + 2:
                    print(f"  [스킵] {name} 데이터 부족")
                    continue

            close = hist["Close"]
            mid = close.rolling(period).mean().iloc[-1]
            std = close.rolling(period).std().iloc[-1]
            upper = mid + std_mult * std
            lower = mid - std_mult * std
            price = close.iloc[-1]

            if pd.isna(mid):
                continue

            pos = "중간"
            signal = None
            if price <= lower:
                pos = "하단 이탈 (매수 관심)"
                signal = "LOWER"
            elif price >= upper:
                pos = "상단 이탈 (과열)"
                signal = "UPPER"

            line = f"{name}({item.get('symbol')}) 종가={price:,.0f} 상단={upper:,.0f} 중간={mid:,.0f} 하단={lower:,.0f} → {pos}"
            print(f"  {line}")
            lines.append(line)

            if signal:
                body = (
                    f"종목: {name}\n코드: {item.get('symbol')}\n"
                    f"현재가: {price:,.0f}\n상단: {upper:,.0f}\n중간: {mid:,.0f}\n하단: {lower:,.0f}\n"
                    f"상태: {pos}\n시각: {datetime.now():%Y-%m-%d %H:%M:%S}"
                )
                emit_alert(f"볼린저 {'하단' if signal == 'LOWER' else '상단'} 신호", body)

        except Exception as e:
            print(f"  [오류] {name}: {e}")

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[완료] {SUMMARY_PATH}")


if __name__ == "__main__":
    run()

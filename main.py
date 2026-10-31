import os
import json
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

from Alerts import emit_alert


def run_bollinger_with_avg():
    try:
        import yfinance as yf
    except Exception as e:
        return [f"yfinance 설치 필요: {e}"]

    alerts = []
    possible_paths = [
        "config/bollinger_watchlist_final.json",
        "bollinger_watchlist_final.json",
        "config/bollinger_watchlist.json",
    ]
    watchlist_path = next((p for p in possible_paths if os.path.exists(p)), None)
    if not watchlist_path:
        return [f"watchlist 없음: {os.listdir('.')}"]

    with open(watchlist_path, encoding="utf-8") as f:
        cfg = json.load(f)

    period = cfg.get("settings", {}).get("period", 20)
    std_mult = cfg.get("settings", {}).get("std", 2)

    for s in cfg.get("symbols", [])[:100]:
        sym = s["symbol"]
        name = s.get("name", sym)
        avg = s.get("avg_price")
        qty = s.get("qty", "")
        try:
            ysym = sym
            market = str(s.get("market", "KR")).upper()
            if sym.startswith("^"):
                ysym = sym
            elif market == "INDEX":
                ysym = s.get("yahoo", sym)
            elif market in ("KOSDAQ", "KQ") and sym.isdigit():
                ysym = sym + ".KQ"
            elif market in ("KR", "KOSPI", "KS") and sym.isdigit():
                ysym = sym + ".KS"
            if sym == "BRK.B":
                ysym = "BRK-B"

            df = yf.download(
                ysym,
                period="3mo",
                interval="1d",
                progress=False,
                auto_adjust=False,
            )
            if len(df) < period:
                continue

            close = df["Close"]
            if hasattr(close, "columns"):
                close = close.iloc[:, 0]
            close = close.dropna()
            if len(close) < period:
                continue

            ma = float(close.rolling(period).mean().iloc[-1])
            std = float(close.rolling(period).std().iloc[-1])
            price = float(close.iloc[-1])
            if std == 0:
                continue

            lower = ma - std_mult * std
            upper = ma + std_mult * std
            percent_b = (price - lower) / (upper - lower) if upper != lower else 0.5
            high_period = float(close.rolling(period).max().iloc[-1])
            low_period = float(close.rolling(period).min().iloc[-1])

            if price <= lower:
                alerts.append(
                    f"[하단 돌파] {name} ({sym}): {price:.2f} / 하단 {lower:.2f}, %B {percent_b:.2f}"
                )
            elif price >= upper:
                alerts.append(
                    f"[상단 돌파] {name} ({sym}): {price:.2f} / 상단 {upper:.2f}, %B {percent_b:.2f}"
                )
            elif percent_b <= 0.05:
                alerts.append(
                    f"[하단 근접] {name} ({sym}): {price:.2f} / {period}일 고점 {high_period:.2f}, 저점 {low_period:.2f}"
                )
            elif percent_b >= 0.95:
                alerts.append(
                    f"[상단 근접] {name} ({sym}): {price:.2f} / {period}일 고점 {high_period:.2f}, 저점 {low_period:.2f}"
                )

            if avg is not None and float(avg) > 0:
                pct = (price - float(avg)) / float(avg) * 100
                if abs(pct) >= 5:
                    alerts.append(
                        f"[평단 대비] {name}: {pct:+.2f}% / 현재 {price:.2f} / 평단 {float(avg):.2f} / 수량 {qty}"
                    )
        except Exception as e:
            alerts.append(f"[종목 오류] {sym}: {type(e).__name__}: {e}")

    return alerts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bollinger", action="store_true")
    args = parser.parse_args()

    if not args.bollinger:
        parser.error("--bollinger 옵션이 필요합니다.")

    alerts = run_bollinger_with_avg()
    if not alerts:
        alerts = ["정상 스캔: 볼린저밴드 및 평단 기준 특이사항이 없습니다."]

    timestamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S KST")
    report = f"실행 시각: {timestamp}\n\n" + "\n".join(alerts)

    os.makedirs("output", exist_ok=True)
    with open("output/summary.txt", "w", encoding="utf-8") as f:
        f.write(report)

    print(report)
    # Telegram sendMessage limit is 4096 characters; split conservatively.
    chunks = [report[i:i + 3500] for i in range(0, len(report), 3500)]
    for index, chunk in enumerate(chunks, start=1):
        title = "NH IPA 모니터링" if len(chunks) == 1 else f"NH IPA 모니터링 ({index}/{len(chunks)})"
        emit_alert(title, chunk)


if __name__ == "__main__":
    main()

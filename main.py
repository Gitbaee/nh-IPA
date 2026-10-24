import os, json, argparse

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
    p = None
    for cand in possible_paths:
        if os.path.exists(cand):
            p = cand
            break
    if not p:
        return [f"watchlist 없음: {os.listdir('.')}"]
    with open(p, encoding="utf-8") as f:
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
            m = s.get("market", "KR")
            if sym.startswith("^"):
                ysym = sym
            elif m == "INDEX":
                ysym = s.get("yahoo", sym)
            elif m == "KR" and sym.isdigit():
                ysym = sym + ".KS"
            if sym == "BRK.B":
                ysym = "BRK-B"
            df = yf.download(ysym, period="3mo", interval="1d", progress=False, auto_adjust=False)
            if len(df) < period:
                continue
            close = df['Close']
            if hasattr(close, 'columns'):
                close = close.iloc[:,0]
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
            percent_b = (price - lower) / (upper - lower) if (upper-lower)!=0 else 0.5
            high_20 = float(close.rolling(period).max().iloc[-1])
            low_20 = float(close.rolling(period).min().iloc[-1])
            if price <= lower:
                alerts.append(f"🔵 [{name} {sym}] 하단 돌파! {price:.0f} < {lower:.0f} %B {percent_b:.2f}")
            elif price >= upper:
                alerts.append(f"🔴 [{name} {sym}] 상단 돌파! {price:.0f} > {upper:.0f} %B {percent_b:.2f}")
            elif percent_b <= 0.05:
                alerts.append(f"🔵 [{name} {sym}] 하단 근접 {price:.0f} (고점 {high_20:.0f} 저점 {low_20:.0f})")
            elif percent_b >= 0.95:
                alerts.append(f"🔴 [{name} {sym}] 상단 근접 {price:.0f} (고점 {high_20:.0f} 저점 {low_20:.0f})")
            if avg and avg>0:
                pct = (price-avg)/avg*100
                if abs(pct) >= 5:
                    emo = "📈" if pct>0 else "📉"
                    alerts.append(f"{emo} [{name}] 평단 {avg:,.0f} 대비 {pct:+.2f}% -> 현재 {price:.0f} ({qty}주)")
        except Exception as e:
            alerts.append(f"[{sym}] 오류: {e}")
    return alerts

def main():
    pa = argparse.ArgumentParser()
    pa.add_argument("--bollinger", action="store_true")
    args = pa.parse_args()
    if args.bollinger:
        alerts = run_bollinger_with_avg()
        if not alerts:
            alerts = ["✅ 정상 스캔 - 전 종목 안정권"]
    else:
        alerts = ["--bollinger 옵션 필요"]
    os.makedirs("output", exist_ok=True)
    with open("output/summary.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(alerts))
    print("\n".join(alerts))

if __name__ == "__main__":
    main()

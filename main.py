# main.py - 퀀트 조건 최종 (1억, 2만주, 마감예상) + 볼린저 통합
import os, json, argparse
from auth import get_token
try:
    from market_data import fetch_realtime_tick
except:
    def fetch_realtime_tick(code): return []

def quant_filter(code, ticks):
    alerts = []
    for t in ticks[-30:]:
        try:
            vol = int(str(t.get("cntg_vol",0)).replace(",","") or 0)
            price = int(str(t.get("stck_prpr",0)).replace(",","") or 0)
        except:
            continue
        amount = vol * price
        hour = str(t.get("bsop_hour",""))

        if vol >= 20000 or amount >= 100_000_000:
            alerts.append(f"[{code}] {hour} 대량 {vol}주 {amount/1e8:.1f}억 @{price}")

        if hour >= "15:20:00" or hour >= "152000":
            alerts.append(f"[{code}] 마감예상 현재 {price} {hour}")
    return alerts

def load_watchlist():
    # config/watchlist.txt 읽기 - 실패하면 기본 3개
    path = "config/watchlist.txt"
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            codes = [line.strip() for line in f if line.strip() and not line.strip().startswith("{")]
            if codes: return codes
    return ["005930","000660","005380"]

def run_bollinger():
    import yfinance as yf
    path = "config/bollinger_watchlist.json"
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    symbols = cfg.get("symbols", [])
    results = []
    for s in symbols:
        sym = s["symbol"]
        name = s["name"]
        try:
            ysym = sym if "." in sym or sym.isalpha() else sym + ".KS"
            # KR은 .KS 붙이기
            if s.get("market") == "KR" and "." not in sym:
                ysym = sym + ".KS"
            df = yf.download(ysym, period="1mo", interval="1d", progress=False)
            if len(df) < 20: continue
            ma = df['Close'].rolling(20).mean().iloc[-1]
            std = df['Close'].rolling(20).std().iloc[-1]
            price = df['Close'].iloc[-1]
            upper = ma + 2*std
            lower = ma - 2*std
            if price <= lower*1.003:
                results.append(f"[{sym} {name}] 하단 터치 {price:.2f} <= {lower:.2f}")
        except Exception as e:
            results.append(f"[{sym}] 조회실패 {e}")
    return results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bollinger", action="store_true")
    parser.add_argument("--minutes", type=int, default=10)
    parser.add_argument("--markets", type=str, default="KR,US")
    args = parser.parse_args()

    token = get_token()
    print("TOKEN OK")

    if args.bollinger:
        alerts = run_bollinger()
        if not alerts:
            alerts = ["볼린저 정상 스캔 - 터치 없음"]
    else:
        codes = load_watchlist()
        print(f"WATCHLIST {codes}")
        all_alerts = []
        for code in codes:
            ticks = fetch_realtime_tick(code)
            if ticks:
                all_alerts += quant_filter(code, ticks)
        alerts = all_alerts if all_alerts else [f"정상 스캔 {len(codes)}종목 특이체결 없음"]

    os.makedirs("output", exist_ok=True)
    with open("output/summary.txt","w",encoding="utf-8") as f:
        f.write("\n".join(alerts))
    print("\n".join(alerts))

if __name__ == "__main__":
    main()

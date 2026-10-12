# main.py - 퀀트 조건 최종 (1억, 2만주, 마감예상)
import os, argparse
from auth import get_token
from market_data import fetch_realtime_tick
def quant_filter(code, ticks):
    alerts = []
    for t in ticks[-30:]:
        try:
            vol = int(str(t.get("cntg_vol",0)).replace(",","") or 0)
            price = int(str(t.get("stck_prpr",0)).replace(",","") or 0)
        except:
            continue
        amount = vol * price
        hour = t.get("bsop_hour","")

        # 1. 2만주 / 2. 1억 이상
        if vol >= 20000 or amount >= 100_000_000:
            alerts.append(f"[{code}] {hour} 대량 {vol}주 {amount/1e8:.1f}억 @{price}")

        # 3. 마감 예상 15:20 이후
        if hour >= "15:20:00" or hour >= "152000":
            alerts.append(f"[{code}] 마감예상 현재 {price} {hour}")

    return alerts

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=10)
    args = parser.parse_args()

    token = get_token()
    print(f"TOKEN OK")

    # 맨날 같은 종목 문제 해결 - 여기 100개로 늘리기
    codes = ["005930","000660","005940","247540","066570","403870","042660","293490","011200","475150","042700","028300","130660"]

    all_alerts = []
    for code in codes:
        ticks = fetch_realtime_tick(code)
        if ticks:
            all_alerts += quant_filter(code, ticks)

    os.makedirs("output", exist_ok=True)
    if not all_alerts:
        all_alerts = [f"정상 스캔 {len(codes)}종목 특이체결 없음"]

    with open("output/summary.txt","w",encoding="utf-8") as f:
        f.write("\n".join(all_alerts))

    print("\n".join(all_alerts))

if __name__ == "__main__":
    main()

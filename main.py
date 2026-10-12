# main.py - 퀀트 조건 추가
import os
from monitoring.auth import get_token
from monitoring.market_data import fetch_realtime_tick

def quant_filter(ticks):
    alerts = []
    for t in ticks[-20:]: # 최근 20틱만 검사
        vol = t.get("cntg_vol", 0)
        price = t.get("stck_prpr", 0)
        amount = vol * price
        hour = t.get("bsop_hour","")

        # 조건 1: 2만주 이상
        # 조건 2: 1억 이상
        if vol >= 20000 or amount >= 100_000_000:
            alerts.append(f"{t['shrn_iscd']} {hour} 대량 {vol}주 {amount/1e8:.1f}억 @ {price}")

        # 조건 3: 마감 예상종가 (15:20 이후)
        if hour >= "15:20:00":
            # 예상가 필드가 따로 있으면 교체: t.get("antc_prpr")
            alerts.append(f"{t['shrn_iscd']} 마감예상 현재 {price} {hour}")

    return alerts

# --- 실행부 ---
token = get_token() # 네가 준 appkey, appsecretkey, client_credentials, oob로 발급
print(f"TOKEN OK expires 24h")

# 고정 3개 말고 -> 거래대금 상위 + 네 보유종목 섞어서 100개 스캔
codes = ["005930","000660","005940","247540","066570","403870","042660"] # 여기에 100개 넣을 예정
all_msgs = []

for code in codes:
    ticks = fetch_realtime_tick(code) # 네가 준 Input_0 {iem_cd: "005940"} 방식
    if ticks:
        all_msgs += quant_filter(ticks)

if all_msgs:
    os.makedirs("output", exist_ok=True)
    with open("output/summary.txt","w",encoding="utf-8") as f:
        f.write("\n".join(all_msgs))
    print("\n".join(all_msgs))

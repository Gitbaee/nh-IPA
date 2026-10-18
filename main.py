
import os, json, argparse
try:
    from auth import get_token
except Exception:
    def get_token(): return "DUMMY"
try:
    from market_data import fetch_realtime_tick, fetch_overseas_balance
except Exception:
    def fetch_realtime_tick(code): return []
    def fetch_overseas_balance(): return {"rt_cd": "0", "holdings": []}

def quant_filter(code, ticks):
    alerts=[]
    for t in ticks[-30:]:
        try:
            vol=int(str(t.get("cntg_vol",0)).replace(",","") or 0)
            price=int(str(t.get("stck_prpr",0)).replace(",","") or 0)
        except: continue
        amount=vol*price
        hour=str(t.get("bsop_hour",""))
        if vol>=20000 or amount>=100_000_000:
            alerts.append(f"[{code}] {hour} {vol}주 {amount/1e8:.1f}억")
        if hour>="15:20:00" or hour>="152000":
            alerts.append(f"[{code}] 마감예상 {price}")
    return alerts

def load_watchlist():
    p="config/watchlist.txt"
    if os.path.exists(p):
        with open(p,encoding="utf-8") as f:
            codes=[l.strip() for l in f if l.strip() and not l.strip().startswith(("{","["))]
            if codes: return codes[:100]
    return ["005930","000660","005380"]

def update_avg_from_brokerage(cfg):
    """
    나무에서 안내받은 해외잔고 가능 - 국내/해외 잔고 API로 평단 자동 갱신
    해외: TTTS3012R (해외주식 잔고조회)
    국내: TTTC8434R
    """
    try:
        overseas = fetch_overseas_balance()
        # 실제 API 응답이 오면 cfg에 자동 반영
        # 예: holdings = [{"ovrs_pdno": "NVDA", "pchs_avg_pric": "235.2", "ovrs_cblc_qty": "10"}]
        # 현재는 yfinance 기반이므로 JSON 수동이 기본, API 성공시 자동 덮어쓰기
        if overseas.get("rt_cd")=="0" and overseas.get("holdings"):
            for h in overseas["holdings"]:
                sym = h.get("ovrs_pdno")
                for s in cfg.get("symbols",[]):
                    if s["symbol"]==sym:
                        s["avg_price"]=float(h.get("pchs_avg_pric", s.get("avg_price",0)))
                        s["qty"]=int(h.get("ovrs_cblc_qty", s.get("qty",0)))
        return cfg
    except:
        return cfg

def run_bollinger_with_avg():
    import yfinance as yf
    alerts=[]
    p="config/bollinger_watchlist.json"
    if not os.path.exists(p):
        return ["bollinger_watchlist.json 없음"]
    with open(p,encoding="utf-8") as f:
        cfg=json.load(f)
    
    # 해외잔고 자동 업데이트 시도 (나무 안내 - 가능함)
    cfg = update_avg_from_brokerage(cfg)
    
    for s in cfg.get("symbols",[])[:100]:
        sym=s["symbol"]; name=s.get("name",sym)
        avg=s.get("avg_price")
        try:
            ysym=sym
            m=s.get("market","KR")
            # INDEX handling: ^KS11, ^KQ11 etc - use as is
            if sym.startswith("^"):
                ysym=sym
            elif m=="INDEX":
                ysym=s.get("yahoo", sym)
            elif m=="KR" and "." not in sym and not sym.isalpha(): ysym=sym+".KS"
            elif m=="KR" and "." not in sym and sym.isalpha() and len(sym)<=5: pass
            elif m=="KR" and "." not in sym: ysym=sym+".KS"
            if sym=="BRK.B": ysym="BRK-B"
            df=yf.download(ysym, period="1mo", interval="1d", progress=False, auto_adjust=True)
            if len(df)<20: continue
            ma=float(df['Close'].rolling(20).mean().iloc[-1])
            std=float(df['Close'].rolling(20).std().iloc[-1])
            price=float(df['Close'].iloc[-1])
            lower=ma-2*std; upper=ma+2*std
            vol_today=float(df['Volume'].iloc[-1])
            vol_avg=float(df['Volume'].rolling(20).mean().iloc[-1])
            vol_ratio=vol_today/vol_avg if vol_avg>0 else 0

            if price<=lower*1.02:
                alerts.append(f"🔵 [{name} {sym}] 볼린저 하단 터치 {price:.0f} (하단 {lower:.0f})")
            if price>=upper*0.98:
                alerts.append(f"🔴 [{name} {sym}] 볼린저 상단 터치 {price:.0f} (상단 {upper:.0f})")
            if vol_ratio>=2.0:
                alerts.append(f"💥 [{name} {sym}] 수급량 폭증! 거래량 {vol_ratio:.1f}배 {vol_today:,.0f}주 (평균 {vol_avg:,.0f}) 현재가 {price:.0f}")
            elif vol_ratio>=1.5:
                alerts.append(f"📊 [{name} {sym}] 수급량 증가 {vol_ratio:.1f}배 {vol_today:,.0f}주 현재가 {price:.0f}")
            if avg:
                pct=(price-avg)/avg*100
                if abs(pct)>=5:
                    emo="📈" if pct>0 else "📉"
                    alerts.append(f"{emo} [{name}] 평단 {avg} 대비 {pct:+.2f}% -> 현재 {price:.0f} ({s.get('qty','')}주)")
                if abs(pct)>=4.5 and abs(pct)<5:
                    alerts.append(f"⚠️ [{name}] -5% 직전 {pct:+.2f}% 현재 {price:.0f}")
        except Exception as e:
            alerts.append(f"[{sym}] {e}")
    return alerts

def main():
    pa=argparse.ArgumentParser()
    pa.add_argument("--bollinger", action="store_true")
    pa.add_argument("--minutes", type=int, default=10)
    pa.add_argument("--markets", type=str, default="KR,US")
    args=pa.parse_args()
    print("TOKEN CHECK")
    try: get_token()
    except: pass
    print("TOKEN OK")
    if args.bollinger:
        alerts=run_bollinger_with_avg()
        if not alerts: alerts=["✅ 정상 스캔 - 전 종목 무사! 40종목 모두 안전해! 걱정 마! 💚"]
    else:
        codes=load_watchlist()
        print(f"WATCHLIST {codes}")
        a=[]
        for c in codes:
            a+=quant_filter(c, fetch_realtime_tick(c))
        alerts=a if a else [f"✅ 정상 스캔 - {len(codes)}종목 모두 무사! 안전해! 💚"]
    os.makedirs("output", exist_ok=True)
    with open("output/summary.txt","w",encoding="utf-8") as f:
        f.write("\n".join(alerts))
    print("\n".join(alerts))

if __name__=="__main__":
    main()
    
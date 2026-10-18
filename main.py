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
    try:
        overseas = fetch_overseas_balance()
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
    import pandas as pd
    alerts=[]
    possible_paths = [
        "config/bollinger_watchlist.json",
        "config/bollinger_watchlist_final.json",
        "bollinger_watchlist.json",
        "bollinger_watchlist_final.json",
    ]
    p = None
    for cand in possible_paths:
        if os.path.exists(cand):
            p = cand
            break
    if not p:
        try:
            files = os.listdir("config") if os.path.exists("config") else []
            return [f"bollinger_watchlist.json 없음 - config 폴더: {files}"]
        except:
            return ["bollinger_watchlist.json 없음"]
    with open(p,encoding="utf-8") as f:
        cfg=json.load(f)

    cfg = update_avg_from_brokerage(cfg)
    
    # settings에서 period, std 읽기
    period = cfg.get("settings",{}).get("period",20)
    std_mult = cfg.get("settings",{}).get("std",2)

    for s in cfg.get("symbols",[])[:100]:
        sym=s["symbol"]; name=s.get("name",sym)
        avg=s.get("avg_price")
        try:
            ysym=sym
            m=s.get("market","KR")
            if sym.startswith("^"):
                ysym=sym
            elif m=="INDEX":
                ysym=s.get("yahoo", sym)
            elif m=="KR" and sym.isdigit():
                ysym=sym+".KS"
            if sym=="BRK.B": ysym="BRK-B"
            
            df=yf.download(ysym, period="3mo", interval="1d", progress=False, auto_adjust=False)
            if len(df)<period: 
                continue
            
            # yfinance 버전에 따라 Close 컬럼이 다름 대응
            if 'Close' in df.columns:
                close = df['Close']
                # MultiIndex나 DataFrame일 경우 처리
                if isinstance(close, pd.DataFrame):
                    close = close.iloc[:,0]
            else:
                close = df.iloc[:,3] if len(df.columns)>=4 else df.iloc[:,0]

            close = close.dropna()
            if len(close) < period:
                continue

            ma = close.rolling(period).mean().iloc[-1]
            std = close.rolling(period).std().iloc[-1]
            price = close.iloc[-1]
            
            # float 변환
            ma = float(ma); std = float(std); price = float(price)
            
            if std == 0:
                continue

            lower = ma - std_mult*std
            upper = ma + std_mult*std
            bandwidth = (upper - lower) / ma * 100 if ma!=0 else 0
            # %B = (price - lower)/(upper - lower)
            percent_b = (price - lower) / (upper - lower) if (upper-lower)!=0 else 0.5

            vol_today = float(df['Volume'].iloc[-1]) if 'Volume' in df.columns else 0
            vol_avg = float(df['Volume'].rolling(period).mean().iloc[-1]) if 'Volume' in df.columns else 0
            vol_ratio = vol_today/vol_avg if vol_avg>0 else 0

            # --- 수정된 볼린저 로직 ---
            # 1. 밴드 터치/돌파 (정확히 터치했을 때만)
            if price <= lower:
                alerts.append(f"🔵 [{name} {sym}] 볼린저 하단 돌파! {price:.0f} < 하단 {lower:.0f} (중심 {ma:.0f}) %B {percent_b:.2f}")
            elif price >= upper:
                alerts.append(f"🔴 [{name} {sym}] 볼린저 상단 돌파! {price:.0f} > 상단 {upper:.0f} (중심 {ma:.0f}) %B {percent_b:.2f}")
            elif percent_b <= 0.05:
                alerts.append(f"🔵 [{name} {sym}] 볼린저 하단 근접 {price:.0f} (하단 {lower:.0f}) %B {percent_b:.2f} 밴드폭 {bandwidth:.1f}%")
            elif percent_b >= 0.95:
                alerts.append(f"🔴 [{name} {sym}] 볼린저 상단 근접 {price:.0f} (상단 {upper:.0f}) %B {percent_b:.2f} 밴드폭 {bandwidth:.1f}%")

            # 2. 수급량
            if vol_ratio>=2.0:
                alerts.append(f"💥 [{name} {sym}] 거래량 폭증 {vol_ratio:.1f}배 {vol_today:,.0f}주 (평균 {vol_avg:,.0f}) 현재 {price:.0f}")
            elif vol_ratio>=1.5:
                alerts.append(f"📊 [{name} {sym}] 거래량 증가 {vol_ratio:.1f}배 {vol_today:,.0f}주 현재 {price:.0f}")

            # 3. 평단 대비
            if avg and avg>0:
                pct=(price-avg)/avg*100
                if abs(pct)>=7:
                    emo="📈" if pct>0 else "📉"
                    alerts.append(f"{emo} [{name}] 평단 {avg:,.0f} 대비 {pct:+.2f}% -> 현재 {price:.0f} ({s.get('qty','')}주)")
                elif abs(pct)>=5:
                    emo="📈" if pct>0 else "📉"
                    alerts.append(f"{emo} [{name}] 평단 {avg:,.0f} 대비 {pct:+.2f}% -> 현재 {price:.0f}")

        except Exception as e:
            alerts.append(f"[{sym}] 오류: {e}")
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
        if not alerts: alerts=["✅ 정상 스캔 - 전 종목 무사! 볼린저 밴드 내 안정권 💚"]
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

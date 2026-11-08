#!/usr/bin/env python3
"""NH PLUG 국내/해외주식 최근 20거래일 종가 시그마를 Telegram으로 전송."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nhplug import call
from Alerts import emit_alert

ROOT = Path(__file__).resolve().parent
WATCHLIST = ROOT / "config" / "bollinger_watchlist.json"
OUTPUT = ROOT / "output"
KST = ZoneInfo("Asia/Seoul")
WINDOW = 20


def get_domestic_rows(code: str) -> list[dict]:
    response = call("/krstock/quote/v1/currentDaily", {
        "market_cd": "UNT",
        "iem_cd": code,
        "array_cnt": "30",
        "view_main_yn": "Y",
    })
    rows = response.get("Output_0") if isinstance(response, dict) else None
    if not isinstance(rows, list) or not rows:
        message = response.get("rsp_msg", "NH 국내 일봉 자료 없음") if isinstance(response, dict) else "응답 형식 오류"
        raise ValueError(str(message)[:120])
    return rows


def get_overseas_rows(ticker: str) -> list[dict]:
    """NH PLUG 해외주식 개별종목 기간별 일봉 조회."""
    end_date = datetime.now(KST).strftime("%Y%m%d")
    response = call("/gbstock/quote/v1/period", {
        "iem_cd": ticker,
        "end_dt": end_date,
        "count": "0030",
        "maxavg": "020",
        "gubun": "3",       # 일봉
        "xtick": "0001",    # 일 조회 단위
        "today_cls": "0",   # 종료일 기준 조회
        "market_cls": "1",  # 정규장
    })
    rows = response.get("Output_0") if isinstance(response, dict) else None
    if not isinstance(rows, list) or not rows:
        message = response.get("rsp_msg", "NH 해외 일봉 자료 없음") if isinstance(response, dict) else "응답 형식 오류"
        raise ValueError(str(message)[:120])
    return rows


def calculate(item: dict) -> dict:
    code = str(item.get("symbol", "")).strip()
    name = str(item.get("name") or code)
    market = str(item.get("market", "KR")).upper()
    try:
        if market == "US":
            rows = get_overseas_rows(code)
            close_field = "close_prc"
            date_fields = ("bsop_date", "date")
        else:
            if not code.isdigit() or len(code) != 6:
                return {"name": name, "code": code, "z": None, "error": "NH 국내 일봉 API 대상 종목코드가 아님"}
            rows = get_domestic_rows(code)
            close_field = "stck_clpr"
            date_fields = ("bsop_date",)

        points = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw = row.get(close_field)
            date = next((row.get(k) for k in date_fields if row.get(k)), "")
            try:
                close = float(str(raw).replace(",", "").strip())
            except (TypeError, ValueError):
                continue
            if math.isfinite(close) and close > 0:
                points.append((str(date), close))
        # API 배열은 최신 날짜 우선으로 반환됩니다. 최신 20 거래일을 사용합니다.
        points = points[:WINDOW]
        if len(points) < WINDOW:
            raise ValueError(f"종가 자료 {len(points)}개: 20개 미달")
        values = [p[1] for p in points]
        mean = sum(values) / WINDOW
        variance = sum((v - mean) ** 2 for v in values) / WINDOW
        std = math.sqrt(variance)
        if not math.isfinite(std) or std <= 0:
            raise ValueError("표준편차 계산 불가")
        latest_date, latest_close = points[0]
        z = (latest_close - mean) / std
        if z >= 2:
            state = "+2σ 이상"
        elif z >= 1:
            state = "+1σ~+2σ"
        elif z <= -2:
            state = "−2σ 이하"
        elif z <= -1:
            state = "−1σ~−2σ"
        else:
            state = "중심선 ±1σ"
        return {"name": name, "code": code, "market": market, "date": latest_date,
                "close": latest_close, "z": z, "state": state, "error": ""}
    except Exception as exc:
        return {"name": name, "code": code, "market": market, "z": None,
                "error": str(exc)[:120]}


def main() -> int:
    os.environ.setdefault("NHPLUG_APP_KEY", os.getenv("NH_APP_KEY", ""))
    os.environ.setdefault("NHPLUG_APP_SECRET", os.getenv("NH_APP_SECRET", ""))
    cfg = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    results = [calculate(item) for item in cfg.get("symbols", [])]
    stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [
        "기준: NH PLUG 국내/해외 최근 20거래일 정규장 종가 | 모집단 표준편차(ddof=0)",
        f"생성: {stamp}",
        "※ 최신 종가의 20일 평균 대비 σ 위치이며 매수·매도 신호 또는 수급값이 아닙니다.",
        "※ 해외 개별종목은 NH PLUG 해외 기간별 일봉 API, 국내는 국내 일자별 API를 사용합니다.",
        "",
    ]
    for r in results:
        if r["z"] is None:
            lines.append(f"• {r['name']} ({r['code']}) | σ: 계산 불가 | {r['error'] or '자료 없음'}")
        else:
            lines.append(f"• {r['name']} ({r['code']}) | {r['date']} 종가 {r['close']:,.2f} | "
                         f"σ {r['z']:+.2f} | {r['state']}")
    report = "\n".join(lines)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "sigma_morning_report.txt").write_text(report + "\n", encoding="utf-8")
    chunks, current = [], ""
    for line in lines:
        if current and len(current) + len(line) + 1 > 3400:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    for i, chunk in enumerate(chunks, 1):
        emit_alert(f"NH PLUG 국내·해외 20일 시그마 ({i}/{len(chunks)})", chunk)
    print(report, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

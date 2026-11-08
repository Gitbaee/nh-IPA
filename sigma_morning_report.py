#!/usr/bin/env python3
"""NH PLUG 기반 최근 20거래일 종가 시그마를 Telegram으로 전송."""
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


def get_rows(code: str) -> list[dict]:
    """NH PLUG 국내주식 일자별 시세. 정규장 종가 기준."""
    response = call("/krstock/quote/v1/currentDaily", {
        "market_cd": "UNT",
        "iem_cd": code,
        "array_cnt": "30",
        "view_main_yn": "Y",
    })
    rows = response.get("Output_0") if isinstance(response, dict) else None
    if not isinstance(rows, list) or not rows:
        message = response.get("rsp_msg", "NH 응답에 일자별 자료가 없습니다.") if isinstance(response, dict) else "응답 형식 오류"
        raise ValueError(str(message)[:120])
    return rows


def calculate(item: dict) -> dict:
    code = str(item.get("symbol", "")).strip()
    name = str(item.get("name") or code)
    if str(item.get("market", "KR")).upper() != "KR":
        return {"name": name, "code": code, "z": None, "error": "NH 국내 일봉 API 대상 아님(해외 종목)"}
    if not code.isdigit() or len(code) != 6:
        return {"name": name, "code": code, "z": None, "error": "국내주식 6자리 종목코드가 아님"}
    try:
        rows = get_rows(code)
        points = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw = row.get("stck_clpr")
            date = row.get("bsop_date", "")
            try:
                close = float(str(raw).replace(",", "").strip())
            except (TypeError, ValueError):
                continue
            if math.isfinite(close) and close > 0:
                points.append((str(date), close))
        # NH currentDaily returns newest-first; select latest 20 trading sessions.
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
        return {"name": name, "code": code, "date": latest_date, "close": latest_close,
                "z": z, "state": state, "error": ""}
    except Exception as exc:
        return {"name": name, "code": code, "z": None, "error": str(exc)[:120]}


def main() -> int:
    # SDK가 NHPLUG_* 변수를 읽도록 기존 Actions secrets도 호환 연결.
    os.environ.setdefault("NHPLUG_APP_KEY", os.getenv("NH_APP_KEY", ""))
    os.environ.setdefault("NHPLUG_APP_SECRET", os.getenv("NH_APP_SECRET", ""))
    cfg = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    items = cfg.get("symbols", [])
    results = [calculate(item) for item in items]
    stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [
        "기준: NH PLUG 국내주식 최근 20거래일 정규장 종가 | 모집단 표준편차(ddof=0)",
        f"생성: {stamp}",
        "※ 최신 종가의 20일 평균 대비 σ 위치입니다. 매수·매도 신호 또는 수급값이 아닙니다.",
        "※ 해외주식/지수는 이 국내 일봉 API 대상이 아니므로 계산 불가로 표시합니다.",
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

    # Telegram message limit is 4096 chars; chunk safely.
    chunks, current = [], ""
    for line in lines:
        if current and len(current) + len(line) + 1 > 3400:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    for i, chunk in enumerate(chunks, 1):
        emit_alert(f"NH PLUG 종목별 20일 시그마 ({i}/{len(chunks)})", chunk)
    print(report, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

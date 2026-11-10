#!/usr/bin/env python3
"""NH PLUG 계좌 실제 보유종목의 최근 20거래일 종가 시그마 보고서."""
from __future__ import annotations

import math
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nhplug import call
from Alerts import emit_alert
from market_data import fetch_domestic_balance, fetch_overseas_balance

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
KST = ZoneInfo("Asia/Seoul")
WINDOW = 20


def _first(row: dict, keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return default


def _number(value: object) -> float:
    return float(str(value).replace(",", "").strip())


def _rows(data: dict, market: str) -> list[dict]:
    """Read only the documented Output_1 holding array; never fall back to a watchlist."""
    rows = data.get("Output_1")
    if not isinstance(rows, list):
        raise RuntimeError(f"NH {market} 잔고 응답에 Output_1 보유종목 배열이 없습니다.")
    return rows


def fetch_account_holdings() -> list[dict]:
    """Fetch current domestic and US holdings from the linked NH account."""
    domestic = fetch_domestic_balance()
    overseas = fetch_overseas_balance()
    result: list[dict] = []

    for row in _rows(domestic, "국내"):
        if not isinstance(row, dict):
            continue
        code = _first(row, ("iem_cd", "pdno", "isu_srt_cd"))
        if not code:
            continue
        qty = _first(row, ("bnc_qty", "hld_qty", "hold_qty", "qty"), "0")
        try:
            if _number(qty) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        result.append({
            "symbol": code.zfill(6),
            "name": _first(row, ("iem_nm", "prdt_name", "isu_nm"), code),
            "market": "KR",
            "qty": qty,
        })

    for row in _rows(overseas, "해외"):
        if not isinstance(row, dict):
            continue
        code = _first(row, ("ovrs_pdno", "iem_cd", "pdno", "symbol"))
        if not code:
            continue
        qty = _first(row, ("ovrs_cblc_qty", "ovrs_hldg_qty", "hld_qty", "qty"), "0")
        try:
            if _number(qty) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        result.append({
            "symbol": code.upper(),
            "name": _first(row, ("iem_nm", "ovrs_item_name", "prdt_name", "name"), code),
            "market": "US",
            "qty": qty,
        })

    # De-duplicate by market + symbol while retaining the first account row.
    unique: dict[tuple[str, str], dict] = {}
    for item in result:
        unique[(item["market"], item["symbol"])] = item
    return list(unique.values())


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
    """NH PLUG 해외 기간별 일봉: 캔들은 Output_1이며 close_prc는 숫자형."""
    end_date = datetime.now(KST).strftime("%Y%m%d")
    response = call("/gbstock/quote/v1/period", {
        "iem_cd": ticker,
        "end_dt": end_date,
        "count": "30",
        "maxavg": "0",
        "gubun": "3",       # 해외 일봉 (국내 일봉 코드와 다름)
        "xtick": "0001",
        "today_cls": "0",
        "market_cls": "1",  # 정규장
    })
    rows = response.get("Output_1") if isinstance(response, dict) else None
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
            close_fields = ("close_prc", "ovrs_nmix_prpr", "last", "clos")
            date_fields = ("trade_date", "bsop_date", "date")
        else:
            if not code.isdigit() or len(code) != 6:
                return {"name": name, "code": code, "z": None, "error": "국내 종목코드 형식 오류"}
            rows = get_domestic_rows(code)
            close_fields = ("stck_clpr", "close_prc")
            date_fields = ("bsop_date", "trade_date", "date")

        points = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw = _first(row, close_fields)
            date = _first(row, date_fields)
            try:
                close = _number(raw)
            except (TypeError, ValueError):
                continue
            if math.isfinite(close) and close > 0:
                points.append((str(date), close))
        # API는 최신 거래일 우선 반환. 동일 날짜 중복은 제거합니다.
        seen: set[str] = set()
        ordered = []
        for point in points:
            if point[0] and point[0] in seen:
                continue
            if point[0]:
                seen.add(point[0])
            ordered.append(point)
        points = ordered[:WINDOW]
        if len(points) < WINDOW:
            raise ValueError(f"유효 종가 {len(points)}개: 최근 20거래일 미달")
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

    try:
        holdings = fetch_account_holdings()
    except Exception as exc:
        # Fail closed: never disguise a failed account lookup as a watchlist scan.
        report = (
            "NH PLUG 실제 계좌 스캔 실패\n"
            f"생성: {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')}\n"
            f"오류: {str(exc)[:180]}\n"
            "고정 관심종목 목록으로 대체 스캔하지 않았습니다."
        )
        OUTPUT.mkdir(parents=True, exist_ok=True)
        (OUTPUT / "sigma_morning_report.txt").write_text(report + "\n", encoding="utf-8")
        emit_alert("NH PLUG 계좌 스캔 실패", report)
        print(report, flush=True)
        return 1

    results = [calculate(item) for item in holdings]
    kr_count = sum(1 for item in holdings if item["market"] == "KR")
    us_count = sum(1 for item in holdings if item["market"] == "US")
    stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [
        f"계좌 기준: NH PLUG 실제 보유종목 | 국내 {kr_count}개 · 해외 {us_count}개 · 합계 {len(holdings)}개",
        "기준: 최근 20거래일 정규장 종가 | 모집단 표준편차(ddof=0)",
        f"생성: {stamp}",
        "※ 고정 관심종목 목록은 사용하지 않습니다. 잔고 API 조회에 실패하면 보고서를 실패 처리합니다.",
        "※ 최신 종가의 20일 평균 대비 σ 위치이며 매수·매도 신호 또는 수급값이 아닙니다.",
        "",
    ]
    for r in results:
        if r["z"] is None:
            lines.append(f"• {r['name']} ({r['code']}, {r['market']}) | σ: 계산 불가 | {r['error'] or '자료 없음'}")
        else:
            lines.append(f"• {r['name']} ({r['code']}, {r['market']}) | {r['date']} 종가 {r['close']:,.2f} | "
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
        emit_alert(f"NH PLUG 실제 계좌 20일 시그마 ({i}/{len(chunks)})", chunk)
    print(report, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

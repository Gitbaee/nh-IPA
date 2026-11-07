#!/usr/bin/env python3
"""Build a holdings-first sigma/short-interest report from saved KRX daily data.

This is an end-of-day historical analysis; it makes no live market calls and does
not infer investor class from broker/member data. Missing observations stay blank.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "shorting"
OUTPUT = ROOT / "output"
FILES = {
    "price": DATA / "daily_prices.csv.gz",
    "volume": DATA / "short_volume.csv.gz",
    "balance": DATA / "short_balance.csv.gz",
}
SUMMARY = OUTPUT / "shorting_analysis_summary.txt"
HOLDINGS_OUT = OUTPUT / "shorting_analysis_holdings.csv"
TOP_OUT = OUTPUT / "shorting_analysis_top_symbols.csv"
DATE_COLS = ["_query_date", "TRD_DD", "trdDd", "BAS_DT", "basDt", "DATE", "date"]
CODE_COLS = ["ISU_SRT_CD", "isuSrtCd", "종목코드(단축)", "종목코드", "ISU_CD", "isuCd", "code"]
NAME_COLS = ["ISU_ABBRV", "ISU_NM", "isuNm", "종목명", "name"]


def load_frame(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, compression="gzip", dtype=str, low_memory=False) if path.exists() else pd.DataFrame()


def first_column(frame: pd.DataFrame, names: list[str]) -> str | None:
    return next((name for name in names if name in frame.columns), None)


def numbers(values: pd.Series) -> pd.Series:
    text = values.astype(str).str.strip().str.replace(",", "", regex=False).str.replace("%", "", regex=False)
    return pd.to_numeric(text.replace({"": pd.NA, "-": pd.NA, "nan": pd.NA, "None": pd.NA}), errors="coerce")


def dates(values: pd.Series) -> pd.Series:
    text = values.astype(str).str.replace(r"[^0-9]", "", regex=True)
    return text.where(text.str.len() >= 8, pd.NA).str[:8]


def norm_name(value: object) -> str:
    return "".join(c.lower() for c in str(value or "") if c.isalnum())


def norm_code(value: object) -> str:
    text = str(value or "").strip().replace(".0", "")
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text.upper()


def load_holdings() -> list[dict]:
    """Use configured KR holdings/watchlist, enriched with known six-digit tickers."""
    records: dict[str, dict] = {}
    dart = ROOT / "config" / "dart_watchlist.json"
    if dart.exists():
        cfg = json.loads(dart.read_text(encoding="utf-8"))
        aliases = cfg.get("domestic_aliases", {})
        for name in cfg.get("domestic_holdings", []):
            record = {"symbol": "", "name": str(name).strip(), "qty": "", "avg_price": "", "owned": True}
            records[norm_name(name)] = record
            for alias in aliases.get(name, []):
                records[norm_name(alias)] = record
    watch = ROOT / "config" / "bollinger_watchlist.json"
    if watch.exists():
        cfg = json.loads(watch.read_text(encoding="utf-8"))
        for item in cfg.get("symbols", []):
            if str(item.get("market", "")).upper() != "KR":
                continue
            code = norm_code(item.get("symbol"))
            if not re.fullmatch(r"\d{6}", code):
                continue
            name = str(item.get("name") or code).strip()
            key = norm_name(name)
            rec = records.get(key) or {"symbol": code, "name": name, "qty": "", "avg_price": "", "owned": False}
            rec.update(symbol=code, name=name, qty=item.get("qty", ""), avg_price=item.get("avg_price", ""))
            rec["owned"] = item.get("qty") not in (None, "", 0, "0") and item.get("avg_price") not in (None, "", 0, "0")
            records[key] = rec
    unique: dict[str, dict] = {}
    for rec in records.values():
        key = rec["symbol"] or norm_name(rec["name"])
        if key:
            if key not in unique or (rec["symbol"] and not unique[key]["symbol"]):
                unique[key] = rec
    return sorted(unique.values(), key=lambda r: (not r["owned"], r["name"].lower()))


def dataset_keys(frame: pd.DataFrame) -> tuple[str | None, str | None, str | None]:
    date_col = first_column(frame, DATE_COLS)
    code_col = None
    for col in CODE_COLS:
        if col in frame:
            sample = frame[col].dropna().astype(str).head(200)
            if len(sample) and sample.map(lambda x: bool(re.fullmatch(r"\d{6}(?:\.0)?", x.strip()))).mean() >= 0.5:
                code_col = col
                break
    return date_col, code_col, first_column(frame, NAME_COLS)


def matching_rows(frame: pd.DataFrame, holding: dict) -> pd.DataFrame:
    date_col, code_col, name_col = dataset_keys(frame)
    if frame.empty or not date_col:
        return pd.DataFrame()
    if holding["symbol"] and code_col:
        matched = frame[frame[code_col].map(norm_code) == holding["symbol"]]
        if not matched.empty:
            return matched.copy()
    if name_col:
        aliases = {"NAVER": "네이버", "삼성전자우": "삼성전자"}
        want = {norm_name(holding["name"])}
        alias = aliases.get(holding["name"])
        if alias:
            want.add(norm_name(alias))
        return frame[frame[name_col].map(norm_name).isin(want)].copy()
    return pd.DataFrame()


def sigma_metrics(frame: pd.DataFrame, holding: dict) -> dict:
    rows = matching_rows(frame, holding)
    date_col = first_column(rows, DATE_COLS)
    price_col = first_column(rows, ["TDD_CLSPRC", "stck_prpr", "close", "Close", "closing_price"])
    if rows.empty or not date_col or not price_col:
        return {"sigma_date": "", "close": None, "mean20": None, "std20": None,
                "minus2": None, "minus1": None, "plus1": None, "plus2": None,
                "zscore": None, "line_event": "자료 없음", "price_5d_pct": None}
    work = pd.DataFrame({"date": dates(rows[date_col]), "close": numbers(rows[price_col])}).dropna()
    # KRX price feed can contain duplicate query rows; one close per security/day.
    work = work.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    work["mean20"] = work["close"].rolling(20, min_periods=20).mean()
    work["std20"] = work["close"].rolling(20, min_periods=20).std(ddof=0)
    work["z"] = (work["close"] - work["mean20"]) / work["std20"].replace(0, pd.NA)
    if work.empty:
        return {"sigma_date": "", "close": None, "mean20": None, "std20": None,
                "minus2": None, "minus1": None, "plus1": None, "plus2": None,
                "zscore": None, "line_event": "자료 없음", "price_5d_pct": None}
    latest = work.iloc[-1]
    if pd.isna(latest["mean20"]) or pd.isna(latest["std20"]):
        event = "20거래일 미만"
        bands = [None] * 4
        zscore = None
    else:
        m, s, z = float(latest["mean20"]), float(latest["std20"]), float(latest["z"])
        bands = [m - 2*s, m - s, m + s, m + 2*s]
        zscore = z
        previous = work.iloc[-2] if len(work) >= 2 else None
        event = "중심선 부근"
        for threshold, label in [(2, "+2σ 상단"), (1, "+1σ 상단"), (-1, "−1σ 하단"), (-2, "−2σ 하단")]:
            if z >= threshold:
                event = label
        if previous is not None and pd.notna(previous["z"]):
            prev_z = float(previous["z"])
            for threshold, label in [(2, "+2σ 상향돌파"), (1, "+1σ 상향돌파"), (-1, "−1σ 하향이탈"), (-2, "−2σ 하향이탈")]:
                if (prev_z < threshold <= z) or (prev_z > threshold >= z):
                    event = label
                    break
    prev5 = work.iloc[-6]["close"] if len(work) >= 6 else None
    change5 = (float(latest["close"]) / float(prev5) - 1) * 100 if prev5 not in (None, 0) else None
    return {"sigma_date": str(latest["date"]), "close": float(latest["close"]),
            "mean20": None if pd.isna(latest["mean20"]) else float(latest["mean20"]),
            "std20": None if pd.isna(latest["std20"]) else float(latest["std20"]),
            "minus2": bands[0], "minus1": bands[1], "plus1": bands[2], "plus2": bands[3],
            "zscore": zscore, "line_event": event, "price_5d_pct": change5}


def latest_metric(frame: pd.DataFrame, holding: dict, candidates: list[str]) -> tuple[float | None, str, float | None]:
    rows = matching_rows(frame, holding)
    date_col, metric_col = first_column(rows, DATE_COLS), first_column(rows, candidates)
    if rows.empty or not date_col or not metric_col:
        return None, "", None
    rows = rows.copy()
    rows["date"] = dates(rows[date_col])
    rows["metric"] = numbers(rows[metric_col])
    rows = rows.dropna(subset=["date", "metric"]).sort_values("date").drop_duplicates("date", keep="last")
    if rows.empty:
        return None, "", None
    recent = rows.tail(5)
    base, last = float(recent.iloc[0]["metric"]), float(recent.iloc[-1]["metric"])
    pct = (last / base - 1) * 100 if len(recent) > 1 and base else None
    return last, str(recent.iloc[-1]["date"]), pct


def fmt(value: object, decimals: int = 0) -> str:
    return "자료 없음" if value is None or pd.isna(value) else f"{float(value):,.{decimals}f}"


def main() -> int:
    data = {key: load_frame(path) for key, path in FILES.items()}
    holdings = load_holdings()
    rows = []
    for holding in holdings:
        sig = sigma_metrics(data["price"], holding)
        vol, vol_date, vol_change = latest_metric(data["volume"], holding, ["CVSRTSELL_TRDVOL", "ACC_TRDVOL", "total_volume"])
        bal, bal_date, bal_change = latest_metric(data["balance"], holding, ["BAL_QTY", "balance_qty"])
        rows.append({**holding, **sig, "short_volume": vol, "volume_date": vol_date,
                     "short_volume_5obs_pct": vol_change, "short_balance": bal,
                     "balance_date": bal_date, "short_balance_5obs_pct": bal_change})
    report = pd.DataFrame(rows)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report.to_csv(HOLDINGS_OUT, index=False, encoding="utf-8-sig")

    lines = ["# 보유종목 시그마 라인·공매도 통합 보고서", "",
             "기준: KRX 저장 일봉 종가, 20거래일 이동평균 ±1σ/±2σ (모집단 표준편차, ddof=0).",
             "종목별 수급(개인·기관·외국인)은 현재 이 보고서에 미연동이며, KRX 실시간 체결과 혼동하지 않습니다.",
             "주식대차는 기준일 다음 영업일 갱신, 공매도 잔고는 T+2 지연 가능. 자료 없음은 0이 아닙니다.", "",
             "| 구분 | 종목 | 기준일 | 종가 | −2σ | −1σ | 중심선 | +1σ | +2σ | z | 라인상태 | 공매도량 | 잔고 |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|"]
    for r in rows:
        lines.append("| {status} | {name} ({symbol}) | {date} | {close} | {m2} | {m1} | {mean} | {p1} | {p2} | {z} | {event} | {vol} | {bal} |".format(
            status="보유" if r["owned"] else "관심", name=r["name"], symbol=r["symbol"] or "코드 미확인",
            date=r["sigma_date"] or "자료 없음", close=fmt(r["close"]), m2=fmt(r["minus2"]), m1=fmt(r["minus1"]),
            mean=fmt(r["mean20"]), p1=fmt(r["plus1"]), p2=fmt(r["plus2"]), z=fmt(r["zscore"], 2),
            event=r["line_event"], vol=fmt(r["short_volume"]), bal=fmt(r["short_balance"])))
    lines.extend(["", "## 해석 기준", "- 라인상태는 통계적 가격 위치/교차 표시이며 매수·매도 지시가 아닙니다.",
                  "- 공매도 거래량·잔고는 시장 전체의 데이터이며 외국인·기관·개인 투자자 구분이 아닙니다.",
                  "- σ 계산은 20개 유효 일봉이 확보된 종목만 유효합니다. 휴장/결측은 0으로 채우지 않습니다.",
                  "- 아직 NH investor flow, 신용잔고 및 투자자별 실시간 데이터는 연동하지 않았습니다."])
    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

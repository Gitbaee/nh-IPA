import io
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

BASE = "https://opendart.fss.or.kr/api"
KEYWORDS = (
    "단일판매ㆍ공급계약체결", "단일판매·공급계약체결", "공급계약",
    "유상증자", "무상증자", "전환사채", "신주인수권부사채", "교환사채",
    "최대주주변경", "주식등의대량보유상황보고서",
    "임원ㆍ주요주주특정증권등소유상황보고서",
    "영업(잠정)실적", "잠정실적", "사업보고서", "반기보고서", "분기보고서",
    "합병", "분할", "소송", "횡령", "배임", "회생절차", "파산",
)

def api_get(path, params, *, binary=False):
    response = requests.get(
        f"{BASE}/{path}",
        params={"crtfc_key": os.environ["DART_API_KEY"], **params},
        timeout=30,
    )
    response.raise_for_status()
    if binary:
        return response.content
    data = response.json()
    status = data.get("status")
    if status not in ("000", "013"):
        raise RuntimeError(f"OpenDART {status}: {data.get('message', 'unknown error')}")
    return data

def load_corp_codes():
    raw = api_get("corpCode.xml", {}, binary=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        xml_name = next((n for n in archive.namelist() if n.lower().endswith(".xml")), None)
        if not xml_name:
            raise RuntimeError("OpenDART corpCode ZIP에 XML 파일이 없습니다.")
        root = ET.fromstring(archive.read(xml_name))
    mapping = {}
    for item in root.findall(".//list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if stock_code and corp_code:
            mapping[stock_code] = corp_code
    return mapping

def main():
    key = os.getenv("DART_API_KEY")
    if not key:
        raise RuntimeError("DART_API_KEY가 GitHub Actions secret에 설정되지 않았습니다.")

    with open("config/bollinger_watchlist.json", encoding="utf-8") as f:
        watchlist = json.load(f).get("symbols", [])
    watched = {}
    for item in watchlist:
        symbol = str(item.get("symbol", "")).strip()
        if re.fullmatch(r"\d{6}", symbol):
            watched[symbol] = item.get("name", symbol)

    corp_codes = load_corp_codes()
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    alerts = []
    for stock_code, name in watched.items():
        corp_code = corp_codes.get(stock_code)
        if not corp_code:
            continue
        data = api_get("list.json", {
            "corp_code": corp_code,
            "bgn_de": today,
            "end_de": today,
            "page_no": 1,
            "page_count": 100,
            "sort": "date",
            "sort_mth": "desc",
        })
        for row in data.get("list", []):
            report = row.get("report_nm", "")
            if not any(word in report for word in KEYWORDS):
                continue
            receipt = row.get("rcept_no", "")
            link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}" if receipt else "링크 없음"
            alerts.append(
                f"• {name} ({stock_code})\n"
                f"  공시: {report}\n"
                f"  접수: {row.get('rcept_dt', today)} / {row.get('flr_nm', '')}\n"
                f"  원문: {link}"
            )

    if alerts:
        from Alerts import emit_alert
        timestamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
        body = f"감시 종목 중요 공시 {len(alerts)}건\n확인 시각: {timestamp}\n\n" + "\n\n".join(alerts)
        os.makedirs("output", exist_ok=True)
        with open("output/dart_summary.txt", "w", encoding="utf-8") as f:
            f.write(body)
        for i in range(0, len(body), 3500):
            emit_alert("DART 중요 공시", body[i:i + 3500])
        print(body)
    else:
        print(f"{today}: 감시 종목에서 설정한 중요 공시가 없습니다.")

if __name__ == "__main__":
    main()

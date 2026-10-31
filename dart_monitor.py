import io
import os
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

BASE = "https://opendart.fss.or.kr/api"
SEOUL = ZoneInfo("Asia/Seoul")
PAGE_COUNT = 100
LOOKBACK_DAYS = 7
MESSAGE_LIMIT = 3400

# 이 키워드는 공시를 제외하기 위한 필터가 아니라 중요 공시를 표시하기 위한 분류 기준입니다.
IMPORTANT_KEYWORDS = (
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
        timeout=40,
    )
    response.raise_for_status()
    if binary:
        return response.content

    data = response.json()
    status = data.get("status")
    if status == "013":
        return data
    if status != "000":
        raise RuntimeError(
            f"OpenDART {status}: {data.get('message', 'unknown error')}"
        )
    return data


def load_listed_companies():
    """corpCode.xml에서 종목코드가 있는 상장사만 추출합니다."""
    raw = api_get("corpCode.xml", {}, binary=True)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            xml_name = next(
                (name for name in archive.namelist()
                 if name.lower().endswith(".xml")),
                None,
            )
            if not xml_name:
                raise RuntimeError("corpCode ZIP에 XML 파일이 없습니다.")
            root = ET.fromstring(archive.read(xml_name))
    except zipfile.BadZipFile as exc:
        raise RuntimeError("OpenDART corpCode 응답이 정상 ZIP 파일이 아닙니다.") from exc

    companies = {}
    for item in root.findall(".//list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        corp_name = (item.findtext("corp_name") or "").strip()
        if len(stock_code) == 6 and stock_code.isdigit() and corp_code:
            companies[corp_code] = {
                "stock_code": stock_code,
                "corp_name": corp_name or stock_code,
            }

    # 목록 다운로드/파싱 이상을 '공시 0건'으로 오인하지 않도록 보호합니다.
    if len(companies) < 1000:
        raise RuntimeError(
            f"상장사 고유번호 목록이 비정상적으로 적습니다: {len(companies)}개"
        )
    return companies


def fetch_all_disclosures(begin_date, end_date, listed_companies):
    """전체 공시를 페이지 끝까지 조회한 뒤 국내 상장사 공시만 남깁니다."""
    params = {
        "bgn_de": begin_date,
        "end_de": end_date,
        "page_no": 1,
        "page_count": PAGE_COUNT,
        "sort": "date",
        "sort_mth": "desc",
    }
    first = api_get("list.json", params)

    if first.get("status") == "013":
        return [], 0, 0

    rows = first.get("list")
    if not isinstance(rows, list):
        raise RuntimeError("OpenDART 공시 목록 응답에 list 배열이 없습니다.")

    try:
        total_pages = int(first.get("total_page", 1))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("OpenDART total_page 값이 올바르지 않습니다.") from exc

    if total_pages < 1:
        raise RuntimeError(f"OpenDART total_page 값이 비정상입니다: {total_pages}")

    all_rows = list(rows)
    for page_no in range(2, total_pages + 1):
        page_params = dict(params, page_no=page_no)
        page_data = api_get("list.json", page_params)
        if page_data.get("status") == "013":
            continue
        page_rows = page_data.get("list")
        if not isinstance(page_rows, list):
            raise RuntimeError(
                f"OpenDART {page_no}페이지 응답에 list 배열이 없습니다."
            )
        all_rows.extend(page_rows)

    listed_rows = []
    seen_receipts = set()
    for row in all_rows:
        corp_code = row.get("corp_code", "")
        company = listed_companies.get(corp_code)
        if not company:
            continue
        receipt = row.get("rcept_no", "")
        if receipt and receipt in seen_receipts:
            continue
        if receipt:
            seen_receipts.add(receipt)
        listed_rows.append((company, row))

    return listed_rows, len(all_rows), total_pages


def build_messages(listed_rows, begin_date, end_date, company_count,
                   total_rows, total_pages, timestamp):
    important_count = 0
    normal_count = 0
    records = []

    for company, row in listed_rows:
        report = (row.get("report_nm") or "공시 제목 확인 필요").strip()
        is_important = any(word in report for word in IMPORTANT_KEYWORDS)
        if is_important:
            important_count += 1
            label = "[중요 키워드]"
        else:
            normal_count += 1
            label = "[일반 공시]"

        receipt = row.get("rcept_no", "")
        link = (
            f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"
            if receipt else "링크 없음"
        )
        records.append(
            f"{label} {company['corp_name']} ({company['stock_code']})\n"
            f"공시명: {report}\n"
            f"접수일: {row.get('rcept_dt', '')} / 제출인: {row.get('flr_nm', '')}\n"
            f"접수번호: {receipt or '확인 필요'}\n"
            f"원문: {link}"
        )

    summary = (
        f"조회 기간: {begin_date} ~ {end_date}\n"
        f"조회 시각: {timestamp}\n"
        f"감시 범위: 국내 상장사 전체 (종목코드 보유 법인)\n"
        f"고유번호 목록: {company_count:,}개 상장사\n"
        f"API 조회: {total_rows:,}건 / {total_pages:,}페이지\n"
        f"상장사 공시: {len(listed_rows):,}건\n"
        f"중요 키워드 공시: {important_count:,}건\n"
        f"일반 공시: {normal_count:,}건\n"
    )

    if not records:
        return [summary + "\n조회 기간에 해당하는 국내 상장사 공시가 없습니다."], important_count, normal_count

    messages = []
    current = summary + "\n[공시 전체 목록]\n\n"
    for record in records:
        addition = record + "\n\n"
        if len(current) + len(addition) > MESSAGE_LIMIT and current.strip():
            messages.append(current.rstrip())
            current = "[DART 공시 목록 계속]\n\n"
        current += addition
    if current.strip():
        messages.append(current.rstrip())

    return messages, important_count, normal_count


def main():
    if not os.getenv("DART_API_KEY"):
        raise RuntimeError("DART_API_KEY가 GitHub Actions secret에 설정되지 않았습니다.")

    now = datetime.now(SEOUL)
    end_date = now.date()
    begin_date = end_date - timedelta(days=LOOKBACK_DAYS)
    begin_text = begin_date.strftime("%Y%m%d")
    end_text = end_date.strftime("%Y%m%d")
    timestamp = now.strftime("%Y-%m-%d %H:%M KST")

    companies = load_listed_companies()
    listed_rows, total_rows, total_pages = fetch_all_disclosures(
        begin_text, end_text, companies
    )

    # 중요 공시만 보내는 필터를 적용하지 않습니다. 일반 공시도 모두 전송합니다.
    messages, important_count, normal_count = build_messages(
        listed_rows, begin_text, end_text, len(companies),
        total_rows, total_pages, timestamp,
    )

    from Alerts import emit_alert
    title = "DART 전체 상장사 공시 감시"
    for message in messages:
        emit_alert(title, message)

    os.makedirs("output", exist_ok=True)
    with open("output/dart_summary.txt", "w", encoding="utf-8") as file:
        file.write("\n\n--- 다음 메시지 ---\n\n".join(messages))

    print(
        f"DART 완료: 상장사 {len(companies)}개, "
        f"공시 {len(listed_rows)}건, 중요 키워드 {important_count}건, "
        f"일반 공시 {normal_count}건, API 페이지 {total_pages}개"
    )


if __name__ == "__main__":
    main()

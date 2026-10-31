import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

BASE = "https://opendart.fss.or.kr/api"
SEOUL = ZoneInfo("Asia/Seoul")
PAGE_COUNT = 100
LOOKBACK_DAYS = 7
MESSAGE_LIMIT = 3400
LISTED_MARKETS = {"Y": "코스피", "K": "코스닥", "N": "코넥스"}

# 중요 공시를 표시하는 분류 기준입니다. 공시를 제외하는 필터로 사용하지 않습니다.
IMPORTANT_KEYWORDS = (
    "단일판매ㆍ공급계약체결", "단일판매·공급계약체결", "공급계약",
    "유상증자", "무상증자", "전환사채", "신주인수권부사채", "교환사채",
    "최대주주변경", "주식등의대량보유상황보고서",
    "임원ㆍ주요주주특정증권등소유상황보고서",
    "영업(잠정)실적", "잠정실적", "사업보고서", "반기보고서", "분기보고서",
    "합병", "분할", "소송", "횡령", "배임", "회생절차", "파산",
)


def api_get(params):
    response = requests.get(
        f"{BASE}/list.json",
        params={"crtfc_key": os.environ["DART_API_KEY"], **params},
        timeout=40,
    )
    response.raise_for_status()
    data = response.json()
    status = data.get("status")
    if status == "013":
        return data
    if status != "000":
        raise RuntimeError(
            f"OpenDART {status}: {data.get('message', 'unknown error')}"
        )
    return data


def fetch_all_listed_disclosures(begin_date, end_date):
    """전체 공시를 끝 페이지까지 조회하고 코스피·코스닥·코넥스만 추립니다."""
    params = {
        "bgn_de": begin_date,
        "end_de": end_date,
        "page_no": 1,
        "page_count": PAGE_COUNT,
        "sort": "date",
        "sort_mth": "desc",
    }
    first = api_get(params)
    if first.get("status") == "013":
        return [], 0, 0

    first_rows = first.get("list")
    if not isinstance(first_rows, list):
        raise RuntimeError("OpenDART 응답에 공시 list 배열이 없습니다.")

    try:
        total_pages = int(first.get("total_page", 1))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("OpenDART total_page 값이 올바르지 않습니다.") from exc
    if total_pages < 1:
        raise RuntimeError(f"OpenDART total_page 값이 비정상입니다: {total_pages}")

    all_rows = list(first_rows)
    for page_no in range(2, total_pages + 1):
        page_data = api_get(dict(params, page_no=page_no))
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
        market_code = (row.get("corp_cls") or "").strip()
        if market_code not in LISTED_MARKETS:
            continue

        receipt = (row.get("rcept_no") or "").strip()
        if receipt and receipt in seen_receipts:
            continue
        if receipt:
            seen_receipts.add(receipt)

        listed_rows.append(row)

    return listed_rows, len(all_rows), total_pages


def build_messages(rows, begin_date, end_date, total_rows, total_pages, timestamp):
    important_count = 0
    normal_count = 0
    company_codes = set()
    records = []

    for row in rows:
        report = (row.get("report_nm") or "공시 제목 확인 필요").strip()
        market_code = (row.get("corp_cls") or "").strip()
        market_name = LISTED_MARKETS.get(market_code, "상장시장")
        company = (row.get("corp_name") or "회사명 확인 필요").strip()
        stock_code = (row.get("stock_code") or "").strip()
        receipt = (row.get("rcept_no") or "").strip()

        if row.get("corp_code"):
            company_codes.add(row["corp_code"])

        is_important = any(word in report for word in IMPORTANT_KEYWORDS)
        if is_important:
            important_count += 1
            label = "[중요 키워드]"
        else:
            normal_count += 1
            label = "[일반 공시]"

        company_label = f"{company} ({stock_code})" if stock_code else company
        link = (
            f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"
            if receipt else "링크 없음"
        )
        records.append(
            f"{label} [{market_name}] {company_label}\n"
            f"공시명: {report}\n"
            f"접수일: {row.get('rcept_dt', '')} / 제출인: {row.get('flr_nm', '')}\n"
            f"접수번호: {receipt or '확인 필요'}\n"
            f"원문: {link}"
        )

    summary = (
        f"조회 기간: {begin_date} ~ {end_date}\n"
        f"조회 시각: {timestamp}\n"
        f"감시 범위: 코스피·코스닥·코넥스 전체 상장사 (DART 법인구분 Y/K/N)\n"
        f"API 조회: {total_rows:,}건 / {total_pages:,}페이지\n"
        f"상장사 공시: {len(rows):,}건 / 공시 제출 상장사 {len(company_codes):,}개\n"
        f"중요 키워드 공시: {important_count:,}건\n"
        f"일반 공시: {normal_count:,}건\n"
    )

    if not records:
        return [
            summary + "\n조회 기간에 해당하는 코스피·코스닥·코넥스 공시가 없습니다."
        ], important_count, normal_count

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

    rows, total_rows, total_pages = fetch_all_listed_disclosures(
        begin_text, end_text
    )
    # 중요 키워드 여부와 관계없이 국내 3개 시장의 모든 공시를 전송합니다.
    messages, important_count, normal_count = build_messages(
        rows, begin_text, end_text, total_rows, total_pages, timestamp
    )

    from Alerts import emit_alert
    for message in messages:
        emit_alert("DART 전체 상장사 공시 감시", message)

    os.makedirs("output", exist_ok=True)
    with open("output/dart_summary.txt", "w", encoding="utf-8") as file:
        file.write("\n\n--- 다음 메시지 ---\n\n".join(messages))

    print(
        f"DART 완료: 상장사 공시 {len(rows)}건, "
        f"중요 키워드 {important_count}건, 일반 공시 {normal_count}건, "
        f"API 조회 {total_rows}건 / {total_pages}페이지"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # 수집 실패를 '공시 0건'으로 오인하지 않도록 텔레그램 오류 알림을 보냅니다.
        try:
            from Alerts import emit_alert
            failed_at = datetime.now(SEOUL).strftime("%Y-%m-%d %H:%M KST")
            emit_alert(
                "DART 공시 감시 오류",
                f"실행 시각: {failed_at}\n"
                f"조회 결과: 확인 불가 (공시 0건이 아닙니다)\n"
                f"오류: {type(exc).__name__}: {str(exc)[:1500]}",
            )
        except Exception as notify_exc:
            print(
                "DART 오류 텔레그램 전송도 실패했습니다: "
                f"{type(notify_exc).__name__}: {notify_exc}"
            )
        raise

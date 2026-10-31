# 공매도·주식대차 데이터 연동 상태

최종 수정: 2026-11-01 (KST)

## 1. 공식 데이터셋에서 확인한 사항

공식 데이터셋: [금융위원회_주식대차정보 (15059612)](https://www.data.go.kr/data/15059612/openapi.do)

공공데이터포털 상세 페이지에서 확인되는 내용:

- REST API, JSON/XML 제공
- 6개 기능: 주식대차종목순위, 주식종목별대차거래현황, 월별대차거래현황, 주식업종별참여현황, 내외국인대차잔고비교, 내외국인대차거래량
- 필수 파라미터 설명상 업종별 참여현황은 basDt, isinCd가 필요하고, 종목별 대차거래현황 등 나머지 기능은 별도 필수 파라미터가 없다고 안내
- basDt를 생략하면 최신 기준일로 설정된다고 안내
- 실시간 데이터가 아니며 기준일 다음 영업일 13시 이후 갱신
- 개발계정 기본 신청 트래픽 10,000건
- 공공누리 제2유형: 출처표시 + 상업적 이용금지. 상업적 이용은 원천 제공기관 한국예탁결제원과 목적·범위에 따른 별도 정보이용계약 필요. 문의: portal@ksd.or.kr

## 2. 공식 상세 가이드의 미확보 항목

포털에는 금융위원회_주식대차정보_활용자가이드.docx가 참고문서로 표시되어 있으나, 이 실행 환경에서 DOCX 원문 다운로드가 되지 않았습니다. 따라서 아래 상세 항목은 아직 금융위원회 공식 문서와 대조 완료된 상태가 아닙니다.

- 서비스의 정확한 요청 URL 및 operation ID
- 각 operation의 필터 파라미터 전체 목록
- pageNo, numOfRows, JSON 응답 형식 파라미터의 공식 명세
- 응답 필드의 정의·단위·널 처리 규칙
- 과거 데이터 제공 시작일 및 기간 조회 한도

독립 공개 코드의 API 카탈로그와 실측 응답 스윕에서는 다음 후보를 확인했습니다. 이는 공식 문서가 아니므로 공식 명세로 단정하지 않습니다.

- 서비스 후보: GetStocLendBorrInfoService_V2
- 종목별 일별 현황 후보: getStItemLendAndBorrStatu_V2
- 응답 필드 후보: basDt, isinCd, isinCdNm, lnbCclStckCnt, lnbRdptStckCnt, lnbRmanStckCnt
- 일반적인 금융위 API 호출 형식 후보: serviceKey, numOfRows, pageNo, resultType=json, basDt

참고용 독립 자료:
- [공개 API 카탈로그·호출 구현](https://github.com/sykang169/google-python-sample/tree/main/mcp/fsc-equity-ops-mcp-server)
- [공개 실측 API 응답 스윕](https://github.com/seongilp/kr-finance-now/blob/main/scripts/raw/sweep.json)

## 3. 안전장치를 둔 수집기 구현

추가 파일: lending_backtest_data.py

- KRX_LENDING_API_KEY를 GitHub Secret에서 환경변수로 전달하도록 workflow에 연결
- API 키 값은 로그에 출력하지 않음
- 대량 수집 전 최근 기준일로 단일 probe를 수행
- probe 응답에서 예상 필드, basDt 필터 적용, ISIN 형식을 검증
- totalCount를 기준으로 pageNo/numOfRows 페이지를 모두 읽고, 실제 행 수가 맞지 않으면 해당 날짜를 저장하지 않음
- 인증·권한·키 만료·호출량·파라미터 오류는 구분하고, 비재시도 오류에서 반복 호출하지 않음
- 일일 호출량 제한과 초당 호출량 제한에 대응. 요청 간격 기본 1초, 페이지 크기 기본 100
- 2026-05-01 이후 거래일을 수집하며, 기존 원자료는 basDt + isinCd로 누적·중복 제거
- 수집일시와 원천 서비스·오퍼레이션을 별도 필드로 보존

저장 경로:
- 원자료: data/lending/stock_lending_daily.csv.gz
- 일자별 상태: output/lending_collection_status.csv
- 실행 요약: output/lending_collection_summary.txt

중요: probe와 전체 수집은 아직 GitHub Actions에서 실행·검증되지 않았습니다. 위 후보 operation/필드/파라미터는 실행 시 실제 응답으로 검증되며, 공식 DOCX 대조 전까지 상세 명세 확정으로 취급하지 않습니다.

## 4. 공매도·대차 비교파일

추가 파일: compare_shorting_lending.py

- 비교키는 기준일 + ISIN으로 한정
- 종목명으로 임의 결합하지 않음
- 단축코드만 있는 경우 daily_prices에서 날짜별 단축코드-ISIN 교차표를 만들 수 있을 때만 사용
- 교차표가 모호하거나 ISIN을 확인할 수 없으면 해당 데이터셋을 제외하고 사유 기록
- 같은 기준일·ISIN에 상충하는 복수 행이 있으면 임의 합산하지 않고 비교 생성 중단
- 결측값은 보정하지 않음

비교파일:
- data/shorting_lending/shorting_lending_comparison.csv.gz
- output/shorting_lending_comparison_summary.txt

대차잔고는 공매도 순보유잔고와 다른 지표입니다. 대차 후 실제 공매도로 사용되지 않은 물량도 있을 수 있으므로 두 수치를 같은 개념으로 해석하지 않습니다.

## 5. 기존 KRX 수집 안전장치

shorting_backtest_data.py:

- CD007 계정 잠금 또는 로그인 응답 JSON 파싱 실패 시 추가 로그인 요청 중단
- 치명 인증 중단을 상태·실패 파일에 기록
- 치명 인증 오류가 발생하더라도 그 전에 수집한 새 행을 저장한 후 비정상 종료
- workflow의 저장 단계는 if: always()로 실행해 공매도 수집 실패가 대차 자료의 커밋을 막지 않도록 변경

## 6. Workflow 변경

.github/workflows/shorting-backtest.yml:

- 대차 수집 단계 추가(Secret이 존재할 때만 실행)
- 대차 단계 실패는 KRX 공매도 수집과 별도로 처리
- 공매도 수집 뒤 비교파일 생성 단계 추가
- Python 문법검사 및 단위 테스트에 신규 파일 포함
- 수집 자료 저장·아티팩트 업로드를 실패 후에도 실행
- 작업 제한시간 240분

## 7. 기존 공매도 데이터의 마지막 확인 상태

2026-10-30 실행 아티팩트 기준 마지막 확인값:

- 공매도 거래량: 192,860행, 실제 조회일 2026-05-04~2026-08-07
- 공매도 잔고: 186,892행, 실제 조회일 2026-05-04~2026-08-06
- 전종목 주가: 201,282행, 실제 조회일 2026-05-01~2026-08-06
- 요청 917건 중 실패 426건
- 2026-08-07 이후 데이터 공백 존재
- 대차 원자료 및 비교파일은 아직 실측 생성되지 않음

## 8. 현재 실행 상태와 다음 확인

- 이전 GitHub Actions 실행 41203845090은 새 수집기·workflow 수정 전 커밋에서 시작된 실행이며, 마지막 조회 때 수집 job이 in_progress였습니다.
- 이 실행의 로그 다운로드는 GitHub에서 404로 실패했습니다. 따라서 이 실행의 결과를 성공으로 간주하지 않습니다.
- GitHub 연결 도구에서 새 workflow_dispatch 실행 기능을 찾지 못해, 이번 수정 커밋을 즉시 실행하지는 못했습니다.
- 다음 예약 실행에서 신규 workflow의 문법검사·단위 테스트·대차 probe·수집 상태·비교파일 생성을 확인해야 합니다.

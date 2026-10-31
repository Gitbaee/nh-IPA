import unittest

from lending_backtest_data import LendingAPIError, EXPECTED_FIELDS, parse_response
from compare_shorting_lending import date_key, is_isin, find_isin_column
import pandas as pd


def success_payload(day="20261030"):
    item = {
        "basDt": day,
        "isinCd": "KR7005930003",
        "isinCdNm": "삼성전자",
        "lnbCclStckCnt": "100",
        "lnbRdptStckCnt": "50",
        "lnbRmanStckCnt": "1000",
    }
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
            "body": {
                "totalCount": 1,
                "pageNo": 1,
                "numOfRows": 100,
                "items": {"item": [item]},
            },
        }
    }


class LendingResponseTests(unittest.TestCase):
    def test_success_payload_has_expected_fields_and_date(self):
        total, rows, _ = parse_response(success_payload(), "20261030")
        self.assertEqual(total, 1)
        self.assertEqual(len(rows), 1)
        self.assertTrue(EXPECTED_FIELDS.issubset(rows[0]))

    def test_basdt_filter_must_be_applied(self):
        with self.assertRaises(LendingAPIError) as ctx:
            parse_response(success_payload("20261029"), "20261030")
        self.assertEqual(ctx.exception.code, "FILTER")

    def test_missing_expected_field_is_rejected(self):
        payload = success_payload()
        del payload["response"]["body"]["items"]["item"][0]["lnbRmanStckCnt"]
        with self.assertRaises(LendingAPIError) as ctx:
            parse_response(payload, "20261030")
        self.assertEqual(ctx.exception.code, "SCHEMA")

    def test_no_data_is_not_treated_as_auth_failure(self):
        payload = {
            "response": {
                "header": {"resultCode": "03", "resultMsg": "NODATA"},
                "body": {"totalCount": 0, "items": {"item": []}},
            }
        }
        total, rows, status = parse_response(payload, "20261030")
        self.assertEqual(total, 0)
        self.assertEqual(rows, [])
        self.assertEqual(status, "NO_DATA")


class ComparisonKeyTests(unittest.TestCase):
    def test_date_key_normalises_iso_date(self):
        self.assertEqual(date_key("2026-10-30"), "20261030")

    def test_isin_validation(self):
        self.assertTrue(is_isin("KR7005930003"))
        self.assertFalse(is_isin("005930"))

    def test_isin_column_detected_by_values(self):
        frame = pd.DataFrame({"ISU_CD": ["KR7005930003", "KR0000000000"], "name": ["A", "B"]})
        self.assertEqual(find_isin_column(frame), "ISU_CD")


if __name__ == "__main__":
    unittest.main()

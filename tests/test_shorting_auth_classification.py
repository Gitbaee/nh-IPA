import unittest

from shorting_backtest_data import krx_auth_failure_kind


class KRXAuthFailureClassificationTests(unittest.TestCase):
    def test_cd007_is_classified_as_locked(self):
        self.assertEqual(
            krx_auth_failure_kind("CD007 패스워드 오류수에 의한 잠금"),
            "locked",
        )

    def test_korean_lock_message_is_classified_as_locked(self):
        self.assertEqual(
            krx_auth_failure_kind("패스워드 오류수에 의한 잠금"),
            "locked",
        )

    def test_non_json_login_response_is_fatal_session_error(self):
        self.assertEqual(
            krx_auth_failure_kind("Login response not JSON (HTTP 200)"),
            "login_response",
        )

    def test_generic_transient_error_is_not_auth_failure(self):
        self.assertIsNone(
            krx_auth_failure_kind("HTTP 502 Bad Gateway"),
        )


if __name__ == "__main__":
    unittest.main()

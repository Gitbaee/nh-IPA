import sys
import time

import requests
from credential_store import get_credentials

TOKEN_URL = "https://api.nhplug.com:8443/oauth2/token"
ERROR_CODE_KEYS = ("error_code", "error", "rt_cd", "msg_cd", "code")
ERROR_MESSAGE_KEYS = ("error_description", "message", "msg", "msg1", "error")

_TOKEN_CACHE = {"token": None, "expires_at": 0.0}


def _response_error(response, app_key, app_secret):
    try:
        body = response.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        body = {}

    body = body if isinstance(body, dict) else {}
    code = next(
        (body.get(key) for key in ERROR_CODE_KEYS if body.get(key)),
        "unavailable",
    )
    message = next(
        (body.get(key) for key in ERROR_MESSAGE_KEYS if body.get(key)),
        "unavailable",
    )

    safe_code = str(code).replace(app_key, "[REDACTED]").replace(app_secret, "[REDACTED]")
    safe_message = (
        str(message)
        .replace(app_key, "[REDACTED]")
        .replace(app_secret, "[REDACTED]")
    )
    print(
        f"NH OAuth 오류: HTTP status={response.status_code}; "
        f"NH response code={safe_code}; message={safe_message}",
        file=sys.stderr,
    )


def get_access_token(force_refresh=False):
    global _TOKEN_CACHE

    now = time.time()
    if not force_refresh and _TOKEN_CACHE["token"] is not None and _TOKEN_CACHE["expires_at"] > now + 30:
        return _TOKEN_CACHE["token"]

    app_key, app_secret = get_credentials()

    data = {
        "appkey": app_key,
        "appsecretkey": app_secret,
        "grant_type": "client_credentials",
        "scope": "oob",
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    response = requests.post(
        TOKEN_URL,
        data=data,
        headers=headers,
        timeout=10,
    )

    if not response.ok:
        _response_error(response, app_key, app_secret)
        response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise RuntimeError(f"NH OAuth 응답이 올바르지 않습니다: {payload}")

    expires_in = int(payload.get("expires_in") or 0)
    _TOKEN_CACHE = {
        "token": payload,
        "expires_at": now + max(expires_in - 30, 30),
    }
    return payload


if __name__ == "__main__":
    result = get_access_token()

    print("Namuh PLUG 인증 성공")
    print("token_type:", result.get("token_type"))
    print("expires_in:", result.get("expires_in"))

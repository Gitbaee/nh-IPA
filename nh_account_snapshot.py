"""NH PLUG account-position snapshot helper.

Uses the repository's existing NH authentication and account secrets. The
endpoint/response fields follow the NH PLUG SDK domestic balance example;
response contents and credentials are never printed or persisted by this
module. This is separate from market-wide credit balance statistics.
"""
from __future__ import annotations

import os
from typing import Any

import requests

from auth import get_token
from credentials_store import get_credentials

BASE_URL = os.getenv("NHPLUG_BASE_URL", "https://api.nhplug.com:8443").rstrip("/")
TIMEOUT = int(os.getenv("MARKET_DATA_HTTP_TIMEOUT", "15"))


def _account_number() -> str:
    account = os.getenv("NHPLUG_DEFAULT_ACCOUNT") or os.getenv("NH_CANO") or os.getenv("NH_ACCOUNT")
    if not account:
        raise RuntimeError("NH account number secret is not configured.")
    return account.strip()


def fetch_domestic_balance_snapshot() -> dict[str, Any]:
    """Return the raw successful balance response to an in-process caller only."""
    app_key, app_secret = get_credentials()
    payload = {
        "Input_0": {
            "act_no": _account_number(),
            "bnc_bse_cd": "5",
            "ltg_aot_dit_cd": "9",
            "aet_bse": "2",
            "qut_dit_cd": "UNT",
            "aly_qut_cd": "1",
        }
    }
    response = requests.post(
        f"{BASE_URL}/krstock/inquiry/v1/balance",
        headers={
            "authorization": f"Bearer {get_token()}",
            "appkey": app_key,
            "appsecret": app_secret,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("NH domestic balance returned an unexpected response type.")
    # Do not log account, token, or response payload. Require a recognizable
    # holdings list so API errors cannot be mistaken for an empty portfolio.
    if not isinstance(data.get("Output_1"), list):
        raise RuntimeError("NH domestic balance response has no holdings array; schema needs review.")
    return data


def normalize_domestic_positions(data: dict[str, Any]) -> list[dict[str, str]]:
    """Select only per-position fields useful for private local calculations."""
    result = []
    for row in data.get("Output_1", []):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("iem_cd") or row.get("pdno") or row.get("isu_srt_cd") or "").strip()
        if not symbol:
            continue
        result.append({
            "symbol": symbol,
            "name": str(row.get("iem_nm") or row.get("prdt_name") or "").strip(),
            "qty": str(row.get("bnc_qty") or row.get("hld_qty") or "").strip(),
            "avg_price": str(row.get("pchs_avg_pric") or row.get("pchs_avg_price") or "").strip(),
            "current_price": str(row.get("prpr") or row.get("stck_prpr") or "").strip(),
        })
    return result

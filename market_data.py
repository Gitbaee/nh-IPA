#!/usr/bin/env python3
"""
market_data.py

- fetch_realtime_tick: placeholder for KRX / NH integration
- fetch_overseas_balance: NH overseas balance API skeleton
- fetch_domestic_balance: NH domestic balance API skeleton
"""
from __future__ import annotations

import os
import logging
from typing import Any, Dict, List

import requests

try:
    from auth import get_token
except Exception:
    def get_token() -> str:
        return "DUMMY"

BASE_URL = os.getenv("NH_BASE_URL", "https://openapi.ebest.co.kr")
HTTP_TIMEOUT = int(os.getenv("MARKET_DATA_HTTP_TIMEOUT", "10"))


def fetch_realtime_tick(code: str) -> List[Dict[str, Any]]:
    """
    KRX / NH 실시간 틱 조회 자리표시자.
    실제 구현을 여기에 붙이면 됩니다.
    """
    try:
        token = get_token()
        if not token:
            return []
        # TODO: add actual KRX/NH API call here
        return []
    except Exception:
        logging.exception("fetch_realtime_tick error for %s", code)
        return []


def fetch_overseas_balance(exchange: str = "NASD") -> Dict[str, Any]:
    """
    NH 해외잔고 조회 예시.
    UAPI docs vary by broker; this is a safe skeleton.
    """
    try:
        token = get_token()
        if not token:
            return {"rt_cd": "1", "error": "no auth token"}

        cano = os.getenv("NH_CANO") or os.getenv("NH_ACCOUNT")
        acnt_prdt_cd = os.getenv("NH_ACNT_PRDT_CD", "01")
        appkey = os.getenv("NH_APP_KEY")
        appsecret = os.getenv("NH_APP_SECRET")

        if not all([appkey, appsecret]):
            logging.warning("NH_APP_KEY or NH_APP_SECRET not set")

        url = f"{BASE_URL}/uapi/overseas-stock/v1/trading/inquire-balance"
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": appkey or "",
            "appsecret": appsecret or "",
            "tr_id": "TTTS3012R",
            "Content-Type": "application/json",
        }
        params = {
            "CANO": cano or "",
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange,
            "TR_CRCY_CD": os.getenv("NH_TR_CRCY_CD", "USD"),
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }

        resp = requests.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            logging.error("overseas balance HTTP %s: %s", resp.status_code, resp.text)
            return {"rt_cd": str(resp.status_code), "error": resp.text}

        try:
            data = resp.json()
        except ValueError:
            return {"rt_cd": "1", "error": "invalid json response", "text": resp.text}

        if isinstance(data, dict):
            if "holdings" in data:
                return data
            if "balance" in data and isinstance(data["balance"], dict) and "holdings" in data["balance"]:
                return {
                    "rt_cd": data.get("rt_cd", "0"),
                    "msg": data.get("msg", ""),
                    "holdings": data["balance"].get("holdings", []),
                }
            if "output1" in data and isinstance(data["output1"], list):
                return {
                    "rt_cd": data.get("rt_cd", "0"),
                    "msg": data.get("msg", ""),
                    "holdings": data["output1"],
                }
        if isinstance(data, list):
            return {"rt_cd": "0", "holdings": data}

        return {"rt_cd": "0", "data": data}
    except requests.RequestException as re:
        logging.exception("HTTP error fetching overseas balance")
        return {"rt_cd": "1", "error": str(re)}
    except Exception as e:
        logging.exception("Unexpected error in fetch_overseas_balance")
        return {"rt_cd": "1", "error": str(e)}


def fetch_domestic_balance() -> Dict[str, Any]:
    """국내 balance API skeleton."""
    try:
        token = get_token()
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": os.getenv("NH_APP_KEY"),
            "appsecret": os.getenv("NH_APP_SECRET"),
            "tr_id": "TTTC8434R",
        }
        return {"rt_cd": "0", "msg": "domestic API ready"}
    except Exception as e:
        return {"error": str(e)}

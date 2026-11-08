#!/usr/bin/env python3
"""NH투자증권 Namuh PLUG 계좌/시세 조회.

실제 API 경로와 필드는 NH 공식 nhplug SDK 예제에 맞춥니다.
NH_CANO 또는 NH_ACCOUNT에는 계좌번호(계좌번호 전체)를 설정합니다.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from nhplug import call

ACCOUNT = os.getenv("NH_CANO") or os.getenv("NH_ACCOUNT") or os.getenv("NHPLUG_DEFAULT_ACCOUNT")


def _require_account() -> str:
    if not ACCOUNT:
        raise RuntimeError("NH_CANO / NH_ACCOUNT / NHPLUG_DEFAULT_ACCOUNT 계좌번호 환경변수가 없습니다.")
    return ACCOUNT


def _response(data: Any) -> Dict[str, Any]:
    """SDK 응답을 원본을 유지한 채 dict로 반환."""
    return data if isinstance(data, dict) else {"data": data}


def fetch_domestic_balance(qut_dit_cd: str = "UNT", aly_qut_cd: str = "1") -> Dict[str, Any]:
    """국내주식 잔고. aly_qut_cd: 1=정규장, 2=전체장."""
    result = call("/krstock/inquiry/v1/balance", {
        "act_no": _require_account(),
        "bnc_bse_cd": "5",
        "ltg_aot_dit_cd": "9",
        "aet_bse": "2",
        "qut_dit_cd": qut_dit_cd,
        "aly_qut_cd": aly_qut_cd,
    })
    return _response(result)


def fetch_overseas_balance(nat_cd: str = "200", cur_cd: str = "USD") -> Dict[str, Any]:
    """해외주식 잔고. nat_cd 200=미국, cur_cd USD."""
    result = call("/gbstock/inquiry/v1/balance", {
        "act_no": _require_account(),
        "qut_iqr_dit_cd": "9",
        "fc_sec_trd_nat_cd": nat_cd,
        "cur_cd": cur_cd,
        "xns_dit_cd": "1",
    })
    return _response(result)


def fetch_realtime_tick(code: str, market_cd: str = "UNT") -> List[Dict[str, Any]]:
    """REST 현재가 조회 (실시간 WebSocket 틱 스트림과는 구분)."""
    result = call("/krstock/quote/v1/currentPrice", {
        "iem_cd": code,
        "market_cd": market_cd,
    })
    if not isinstance(result, dict):
        return [{"data": result}]
    output = result.get("Output_0")
    if isinstance(output, list):
        return output
    if isinstance(output, dict):
        return [output]
    return [result]

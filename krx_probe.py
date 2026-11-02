#!/usr/bin/env python3
"""Minimal KRX probe for validating the current API/session flow.

This script does not write or mutate existing historical data. It makes a single
probe against the short-selling / balance / daily-price endpoint for a selected
recent trading day and exits non-zero if the response is not a valid JSON record
set.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

from krx_data_api import fetch

TARGET_DAY = os.getenv("KRX_PROBE_DAY", "20260819")


def main() -> int:
    if not os.getenv("KRX_ID") or not os.getenv("KRX_PW"):
        print("[PROBE] KRX_ID and KRX_PW are required.", file=sys.stderr)
        return 2

    print(f"[PROBE] start day={TARGET_DAY}", flush=True)
    checks = [
        ("short_volume", "short_selling_individual", {"trdDd": TARGET_DAY, "mktId": "STK"}),
        ("short_balance", "short_balance_individual", {"trdDd": TARGET_DAY, "mktTpCd": "1"}),
        ("daily_prices", "all_stock_price", {"trdDd": TARGET_DAY, "mktId": "ALL"}),
    ]

    for name, endpoint, params in checks:
        try:
            print(f"[PROBE] calling {endpoint} with {params}", flush=True)
            df = fetch(endpoint, auth=True, **params)
            print(f"[PROBE] {name} rows={len(df) if df is not None else 0}", flush=True)
            print(df.head(3).to_string(index=False) if df is not None and not df.empty else "[PROBE] empty result")
        except Exception as exc:
            print(f"[PROBE][FAIL] {name} endpoint={endpoint} error={exc}", file=sys.stderr, flush=True)
            return 1

    print("[PROBE] all checks passed in total response format.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

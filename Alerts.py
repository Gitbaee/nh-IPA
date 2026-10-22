import os
import requests
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AlertState:
    last_foreign_net: int = 0
    last_alert_time: Optional[float] = None


def process_foreign_net_buy_alert(
    state: AlertState,
    foreign_net: int,
    previous: int,
    threshold: int = 500,
    ratio_threshold: float = 1.5,
) -> bool:
    """외국계 순매수가 급증했는지 판정"""
    state.last_foreign_net = foreign_net

    if foreign_net < threshold:
        return False

    # 이전 값이 0이거나 작으면 단순 임계값만 체크
    if previous <= 0:
        return foreign_net >= threshold

    # 비율 증가 체크
    if foreign_net >= previous * ratio_threshold:
        return True

    # 절대량 큰 증가
    if foreign_net - previous >= threshold:
        return True

    return False


def emit_alert(title: str, body: str) -> None:
    """텔레그램으로 알림 전송 (실패해도 프로그램은 계속 진행)"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

    message = f"🔔 *{title}*\n\n{body}"

    # 콘솔에도 출력
    print("\n" + "=" * 50)
    print(f"[ALERT] {title}")
    print(body)
    print("=" * 50 + "\n")

    if not token or not chat_id:
        print("[알림] TELEGRAM_BOT_TOKEN 또는 CHAT_ID가 없어 텔레그램 전송 생략")
        return

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        if resp.ok:
            print("[알림] 텔레그램 전송 성공")
        else:
            print(f"[알림] 텔레그램 전송 실패: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[알림] 텔레그램 예외: {type(e).__name__}: {e}")

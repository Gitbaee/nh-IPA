import os
import time
import requests

_LAST_SEND_AT = 0.0
MIN_SEND_INTERVAL_SECONDS = 1.1
MAX_RATE_LIMIT_RETRIES = 3


def emit_alert(title: str, body: str) -> None:
    """Send a Telegram message, respecting per-chat pacing and Telegram retry_after."""
    global _LAST_SEND_AT

    token = (
        os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("TELEGRAM_TOKEN")
        or os.getenv("BOT_TOKEN")
    )
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

    if not token:
        raise RuntimeError(
            "Telegram bot token is missing. Add TELEGRAM_BOT_TOKEN (or TELEGRAM_TOKEN) "
            "to GitHub Actions secrets."
        )
    if not chat_id:
        raise RuntimeError(
            "Telegram chat ID is missing. Add TELEGRAM_CHAT_ID (or CHAT_ID) "
            "to GitHub Actions secrets."
        )

    message = f"[{title}]\\n\\n{body}"
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        # Avoid bursts when one workflow sends multiple DART message chunks.
        wait_for_spacing = MIN_SEND_INTERVAL_SECONDS - (time.monotonic() - _LAST_SEND_AT)
        if wait_for_spacing > 0:
            time.sleep(wait_for_spacing)

        response = requests.post(
            url,
            json={"chat_id": chat_id, "text": message},
            timeout=30,
        )
        _LAST_SEND_AT = time.monotonic()

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if response.ok and payload.get("ok"):
            print(f"[알림] 텔레그램 전송 성공: {title}")
            return

        if response.status_code == 429:
            parameters = payload.get("parameters") or {}
            retry_after = parameters.get("retry_after")
            try:
                delay = max(1, int(retry_after))
            except (TypeError, ValueError):
                delay = 60

            if attempt < MAX_RATE_LIMIT_RETRIES:
                print(
                    f"[WARN] Telegram rate limit (429). "
                    f"Retrying after {delay}s; attempt {attempt + 1}/"
                    f"{MAX_RATE_LIMIT_RETRIES}."
                )
                time.sleep(delay + 1)
                continue

            raise RuntimeError(
                f"Telegram sendMessage rate limit persisted after "
                f"{MAX_RATE_LIMIT_RETRIES} retries; retry_after={delay}s."
            )

        # Do not print the request URL: it contains the bot token.
        detail = payload.get("description") or response.text[:300]
        raise RuntimeError(
            f"Telegram sendMessage failed: HTTP {response.status_code}; "
            f"response={detail}"
        )

    raise RuntimeError("Telegram sendMessage failed after retry handling.")

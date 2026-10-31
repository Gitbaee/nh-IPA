import os
import requests


def emit_alert(title: str, body: str) -> None:
    """Send a plain-text Telegram message; fail the job if delivery is not possible."""
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

    message = f"[{title}]\n\n{body}"
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    response = requests.post(
        url,
        json={"chat_id": chat_id, "text": message},
        timeout=20,
    )
    if not response.ok:
        # Do not print the request URL: it contains the bot token.
        raise RuntimeError(
            f"Telegram sendMessage failed: HTTP {response.status_code}; "
            f"response={response.text[:300]}"
        )

    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError("Telegram API returned ok=false.")

    print(f"[알림] 텔레그램 전송 성공: {title}")

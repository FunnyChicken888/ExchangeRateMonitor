"""
Telegram notification sender.

Uses the Telegram Bot API sendMessage endpoint:
  POST https://api.telegram.org/bot{token}/sendMessage

Message format example:
  ┌─────────────────────────────────┐
  │ 📈 價差上穿 0.30                 │
  │                                 │
  │ MAX  賣出：31.85 TWD             │
  │ 銀行 賣出：31.55 TWD             │
  │ 當前價差：+0.30 TWD              │
  │                                 │
  │ 🕐 2025-01-15 14:32:05 (台北)   │
  └─────────────────────────────────┘
"""

import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT  = 10


def send_event(
    config: dict,
    threshold: float,
    direction: str,
    bank_sell: float,
    max_sell: float,
    spread: float,
) -> bool:
    """
    Send a threshold-crossing event notification via Telegram.

    Args:
        config:    The "telegram" section from config.json.
        threshold: The threshold level that was crossed.
        direction: "up" or "down".
        bank_sell: NextBank USD sell rate.
        max_sell:  MAX USDT/TWD sell rate.
        spread:    Current spread value.

    Returns:
        True if the message was sent successfully, False otherwise.
    """
    if not config.get("enabled", True):
        logger.debug("Telegram notifications disabled — skipping.")
        return False

    token   = config["bot_token"]
    chat_id = config["chat_id"]

    message = _format_message(threshold, direction, bank_sell, max_sell, spread)

    url = _API_BASE.format(token=token)
    payload = {
        "chat_id":    chat_id,
        "text":       message,
        "parse_mode": "HTML",
    }

    try:
        resp = requests.post(url, json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            logger.info(
                "Telegram sent: threshold=%.4f direction=%s", threshold, direction
            )
            return True
        else:
            logger.error("Telegram API error: %s", data)
            return False
    except requests.RequestException as exc:
        logger.error("Telegram request failed: %s", exc)
        return False


def send_startup(config: dict) -> bool:
    """
    Send a startup notification so you know the monitor is running.

    Args:
        config: The "telegram" section from config.json.

    Returns:
        True if sent successfully, False otherwise.
    """
    if not config.get("enabled", True):
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = (
        "🚀 <b>ExchangeRateMonitor 已啟動</b>\n\n"
        f"🕐 {now}\n"
        "系統開始監測 MAX USDT/TWD 與 NextBank USD 價差。"
    )

    token   = config["bot_token"]
    chat_id = config["chat_id"]
    url     = _API_BASE.format(token=token)
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}

    try:
        resp = requests.post(url, json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("ok", False)
    except requests.RequestException as exc:
        logger.warning("Startup notification failed: %s", exc)
        return False


def send_error_alert(config: dict, error_msg: str) -> bool:
    """
    Send an error alert when the monitor encounters a critical failure.

    Args:
        config:    The "telegram" section from config.json.
        error_msg: Short description of the error.

    Returns:
        True if sent successfully, False otherwise.
    """
    if not config.get("enabled", True):
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = (
        "⚠️ <b>ExchangeRateMonitor 錯誤警報</b>\n\n"
        f"🕐 {now}\n"
        f"<code>{_escape_html(error_msg)}</code>"
    )

    token   = config["bot_token"]
    chat_id = config["chat_id"]
    url     = _API_BASE.format(token=token)
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}

    try:
        resp = requests.post(url, json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("ok", False)
    except requests.RequestException as exc:
        logger.warning("Error alert notification failed: %s", exc)
        return False


# ── Internal helpers ───────────────────────────────────────────────────────────

def _format_message(
    threshold: float,
    direction: str,
    bank_sell: float,
    max_sell: float,
    spread: float,
) -> str:
    """Build a human-readable HTML message for a crossing event."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if direction == "up":
        icon  = "📈"
        label = "上穿"
    else:
        icon  = "📉"
        label = "下穿"

    spread_sign = "+" if spread >= 0 else ""

    return (
        f"{icon} <b>價差{label} {threshold:+.2f} TWD</b>\n"
        "\n"
        f"MAX  賣出：<code>{max_sell:.4f} TWD</code>\n"
        f"銀行 賣出：<code>{bank_sell:.4f} TWD</code>\n"
        f"當前價差：<code>{spread_sign}{spread:.4f} TWD</code>\n"
        "\n"
        f"🕐 {now}"
    )


def _escape_html(text: str) -> str:
    """Escape special HTML characters for Telegram HTML parse mode."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "<")
            .replace(">", ">")
    )

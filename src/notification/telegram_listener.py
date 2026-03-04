"""
Telegram command listener — polls getUpdates and handles bot commands.

Commands:
  /spread  — fetch live prices and show current spread
  /status  — show system health (last update, errors, cached prices)
  /history — show thresholds already crossed today

Design:
  - Runs as a daemon thread (does not block main loop)
  - Uses long-polling: GET getUpdates?timeout=30
  - Thread safety: reads shared_state dict (written only by main thread)
  - Sends replies via sendMessage (HTML parse_mode)
  - Only responds to messages from the configured chat_id
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_POLL_TIMEOUT    = 30   # seconds Telegram holds connection open
_REQUEST_TIMEOUT = 35   # HTTP socket timeout — must exceed _POLL_TIMEOUT
_RETRY_DELAY     = 5    # seconds to wait after a network error before retrying
_MAX_RETRY_DELAY = 60   # seconds — cap on exponential back-off


class TelegramCommandListener:
    """
    Background daemon thread that polls Telegram getUpdates and dispatches
    /spread, /status, and /history commands.
    """

    def __init__(self, tg_config: dict, config: dict, shared_state: dict) -> None:
        self._tg_config    = tg_config
        self._config       = config
        self._shared_state = shared_state

        self._token   = tg_config["bot_token"]
        self._chat_id = str(tg_config["chat_id"])
        self._enabled = tg_config.get("enabled", True)

        self._offset: Optional[int] = None
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="TelegramListener",
            daemon=True,
        )

    # ── Public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the listener thread. No-op if Telegram is disabled."""
        if not self._enabled:
            logger.info("Telegram disabled — command listener not started.")
            return
        logger.info("Starting Telegram command listener thread.")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the listener to stop and wait for it to exit."""
        logger.info("Stopping Telegram command listener...")
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("Listener thread did not exit within %.1fs.", timeout)

    # ── Internal: main loop ───────────────────────────────────────────────

    def _run(self) -> None:
        """Thread entry point. Polls getUpdates in a loop."""
        logger.info("Telegram listener thread started.")
        retry_delay = _RETRY_DELAY

        while not self._stop_event.is_set():
            try:
                updates = self._get_updates()
                retry_delay = _RETRY_DELAY  # reset on success
                for update in updates:
                    self._handle_update(update)
            except requests.RequestException as exc:
                logger.warning(
                    "Listener poll error: %s — retrying in %ds.", exc, retry_delay
                )
                for _ in range(retry_delay):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)
                retry_delay = min(retry_delay * 2, _MAX_RETRY_DELAY)
            except Exception as exc:
                logger.error("Listener unexpected error: %s", exc, exc_info=True)
                time.sleep(_RETRY_DELAY)

        logger.info("Telegram listener thread exited.")

    # ── Internal: Telegram API ────────────────────────────────────────────

    def _get_updates(self) -> list:
        """Call getUpdates with long-polling. Returns list of update dicts."""
        params: dict = {"timeout": _POLL_TIMEOUT, "allowed_updates": ["message"]}
        if self._offset is not None:
            params["offset"] = self._offset

        url  = f"https://api.telegram.org/bot{self._token}/getUpdates"
        resp = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("ok"):
            logger.error("getUpdates returned not-ok: %s", data)
            return []

        updates = data.get("result", [])
        if updates:
            self._offset = updates[-1]["update_id"] + 1

        return updates

    def _send_reply(self, text: str) -> None:
        """Send a message to the configured chat_id."""
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {
            "chat_id":    self._chat_id,
            "text":       text,
            "parse_mode": "HTML",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            if not resp.json().get("ok"):
                logger.error("sendMessage failed: %s", resp.json())
        except requests.RequestException as exc:
            logger.error("sendMessage request error: %s", exc)

    # ── Internal: update dispatch ─────────────────────────────────────────

    def _handle_update(self, update: dict) -> None:
        """Route a single update to the correct command handler."""
        message = update.get("message", {})
        if not message:
            return

        # Security: only respond to the configured chat
        from_chat = str(message.get("chat", {}).get("id", ""))
        if from_chat != self._chat_id:
            logger.debug("Ignoring message from unknown chat %s.", from_chat)
            return

        text = message.get("text", "").strip()
        if not text.startswith("/"):
            return  # not a command — ignore

        # Strip optional @botname suffix (added by Telegram in group chats)
        command = text.split()[0].split("@")[0].lower()
        logger.info("Command received: %s", command)

        if command == "/spread":
            self._cmd_spread()
        elif command == "/status":
            self._cmd_status()
        elif command == "/history":
            self._cmd_history()
        elif command == "/version":
            self._cmd_version()
        else:
            self._send_reply(
                "未知指令。可用指令：\n"
                "/spread — 即時價差\n"
                "/status — 系統狀態\n"
                "/history — 今日已通知門檻\n"
                "/version — 目前執行版本"
            )

    # ── Command handlers ──────────────────────────────────────────────────

    def _cmd_spread(self) -> None:
        """Fetch live prices and report the current spread."""
        try:
            from src.data.bank_of_taiwan import fetch_usd_sell
            from src.data.max_exchange   import fetch_usdt_twd_sell
            from src.engine.spread       import calculate as calculate_spread

            bank_sell = fetch_usd_sell(self._config["bank_of_taiwan"])
            max_sell  = fetch_usdt_twd_sell(self._config["max_exchange"])
            spread    = calculate_spread(max_sell, bank_sell)

            spread_sign = "+" if spread >= 0 else ""
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            text = (
                "📊 <b>即時價差查詢</b>\n"
                "\n"
                f"MAX  賣出：<code>{max_sell:.4f} TWD</code>\n"
                f"銀行 賣出：<code>{bank_sell:.4f} TWD</code>\n"
                f"當前價差：<code>{spread_sign}{spread:.4f} TWD</code>\n"
                "\n"
                f"🕐 {now}"
            )
        except Exception as exc:
            logger.error("/spread fetch error: %s", exc, exc_info=True)
            text = (
                "⚠️ <b>即時價差查詢失敗</b>\n"
                f"<code>{_escape_html(str(exc))}</code>"
            )

        self._send_reply(text)

    def _cmd_status(self) -> None:
        """Show system health using cached shared_state values."""
        state = self._shared_state

        bank_sell = state.get("last_bank_sell")
        max_sell  = state.get("last_max_sell")
        spread    = state.get("last_spread")
        last_upd  = state.get("last_update_time")
        errors    = state.get("consecutive_errors", 0)

        if last_upd is not None:
            elapsed_s   = int((datetime.now() - last_upd).total_seconds())
            elapsed_str = _format_elapsed(elapsed_s)
            upd_str     = last_upd.strftime("%Y-%m-%d %H:%M:%S")
        else:
            elapsed_str = "N/A"
            upd_str     = "尚無資料"

        if errors == 0:
            health_icon = "✅"
            health_text = "正常"
        elif errors < 3:
            health_icon = "⚠️"
            health_text = f"異常 (連續 {errors} 次錯誤)"
        else:
            health_icon = "🔴"
            health_text = f"嚴重異常 (連續 {errors} 次錯誤)"

        if bank_sell is not None:
            price_lines = (
                f"MAX  賣出：<code>{max_sell:.4f} TWD</code>\n"
                f"銀行 賣出：<code>{bank_sell:.4f} TWD</code>\n"
                f"最近價差：<code>{spread:+.4f} TWD</code>\n"
            )
        else:
            price_lines = "最近價格：<code>尚無資料</code>\n"

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text = (
            f"{health_icon} <b>系統狀態</b>\n"
            "\n"
            f"狀態：{health_text}\n"
            f"最後更新：{upd_str}\n"
            f"距今：{elapsed_str}\n"
            "\n"
            f"{price_lines}"
            "\n"
            f"🕐 {now}"
        )

        self._send_reply(text)

    def _cmd_history(self) -> None:
        """Show thresholds already crossed today from cached shared_state."""
        notified = list(self._shared_state.get("notified_thresholds_today", []))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not notified:
            text = (
                "📋 <b>今日已通知門檻</b>\n"
                "\n"
                "今日尚未觸發任何門檻。\n"
                "\n"
                f"🕐 {now}"
            )
        else:
            lines = "\n".join(
                f"  • <code>{t:+.2f} TWD</code>" for t in sorted(notified, reverse=True)
            )
            text = (
                f"📋 <b>今日已通知門檻（{len(notified)} 個）</b>\n"
                "\n"
                f"{lines}\n"
                "\n"
                f"🕐 {now}"
            )

        self._send_reply(text)

    def _cmd_version(self) -> None:
        """Show current git commit info."""
        import subprocess
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        def _git(args):
            r = subprocess.run(
                ["git"] + args,
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip() if r.returncode == 0 else "N/A"

        commit  = _git(["rev-parse", "--short", "HEAD"])
        branch  = _git(["rev-parse", "--abbrev-ref", "HEAD"])
        tag     = _git(["describe", "--tags", "--exact-match", "HEAD"]) or "（無 tag）"
        subject = _git(["log", "-1", "--format=%s"])
        date    = _git(["log", "-1", "--format=%ci"])

        text = (
            "🔖 <b>目前執行版本</b>\n"
            "\n"
            f"Commit：<code>{commit}</code>\n"
            f"Branch：<code>{branch}</code>\n"
            f"Tag：<code>{tag}</code>\n"
            f"訊息：{_escape_html(subject)}\n"
            f"時間：<code>{date}</code>\n"
            "\n"
            f"🕐 {now}"
        )
        self._send_reply(text)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_elapsed(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} 秒前"
    elif seconds < 3600:
        return f"{seconds // 60} 分 {seconds % 60} 秒前"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h} 小時 {m} 分前"

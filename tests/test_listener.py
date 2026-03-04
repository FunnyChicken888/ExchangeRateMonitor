"""
TelegramCommandListener 單元測試（不需要外部服務，全部使用 mock）

測試範圍：
  1. _get_updates      — 正常 / 網路錯誤 / not-ok 回應
  2. _handle_update    — chat_id 過濾 / 指令 dispatch / 未知指令
  3. _cmd_spread       — 成功回傳 / fetch 失敗時的錯誤訊息
  4. _cmd_status       — 無資料 / 正常 / 異常狀態
  5. _cmd_history      — 無門檻 / 有門檻
  6. start/stop        — enabled=False 不啟動 / stop 設定 Event
"""

import os
import sys
import threading
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.notification.telegram_listener import (
    TelegramCommandListener,
    _escape_html,
    _format_elapsed,
)


# ── 測試輔助工具 ───────────────────────────────────────────────────────────────

def make_listener(tg_config=None, config=None, shared_state=None):
    """建立一個 TelegramCommandListener 實例（不啟動執行緒）。"""
    tg = tg_config or {
        "bot_token": "FAKE_TOKEN",
        "chat_id":   "12345",
        "enabled":   True,
    }
    cfg = config or {
        "bank_of_taiwan": {},
        "max_exchange":   {},
    }
    ss = shared_state or {
        "last_bank_sell":            None,
        "last_max_sell":             None,
        "last_spread":               None,
        "last_update_time":          None,
        "consecutive_errors":        0,
        "notified_thresholds_today": [],
    }
    return TelegramCommandListener(tg, cfg, ss)


def make_update(text: str, chat_id: str = "12345", update_id: int = 1) -> dict:
    """建立一個模擬的 Telegram update dict。"""
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "text": text,
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# 1. _get_updates 測試
# ══════════════════════════════════════════════════════════════════════════════

class TestGetUpdates(unittest.TestCase):

    def setUp(self):
        self.listener = make_listener()

    @patch("src.notification.telegram_listener.requests.get")
    def test_returns_updates_and_advances_offset(self, mock_get):
        """正常回應應回傳 updates 並更新 offset。"""
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = {
            "ok": True,
            "result": [
                {"update_id": 10, "message": {}},
                {"update_id": 11, "message": {}},
            ],
        }

        updates = self.listener._get_updates()
        self.assertEqual(len(updates), 2)
        self.assertEqual(self.listener._offset, 12)  # max(10,11) + 1

    @patch("src.notification.telegram_listener.requests.get")
    def test_empty_result_does_not_change_offset(self, mock_get):
        """空結果（long-poll timeout）不應改變 offset。"""
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = {"ok": True, "result": []}

        self.listener._offset = 5
        updates = self.listener._get_updates()
        self.assertEqual(updates, [])
        self.assertEqual(self.listener._offset, 5)

    @patch("src.notification.telegram_listener.requests.get")
    def test_not_ok_response_returns_empty(self, mock_get):
        """Telegram 回傳 ok=false 時應回傳空列表（不拋例外）。"""
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = {
            "ok":          False,
            "description": "Unauthorized",
        }

        updates = self.listener._get_updates()
        self.assertEqual(updates, [])

    @patch("src.notification.telegram_listener.requests.get")
    def test_offset_sent_as_param(self, mock_get):
        """已設定 offset 時，應作為 query 參數傳送。"""
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = {"ok": True, "result": []}

        self.listener._offset = 99
        self.listener._get_updates()

        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"]["offset"], 99)


# ══════════════════════════════════════════════════════════════════════════════
# 2. _handle_update 測試
# ══════════════════════════════════════════════════════════════════════════════

class TestHandleUpdate(unittest.TestCase):

    def setUp(self):
        self.listener = make_listener()
        # 攔截所有指令 handler 和 _send_reply
        self.listener._cmd_spread  = MagicMock()
        self.listener._cmd_status  = MagicMock()
        self.listener._cmd_history = MagicMock()
        self.listener._send_reply  = MagicMock()

    def test_wrong_chat_id_is_ignored(self):
        """來自不同 chat_id 的訊息應靜默忽略。"""
        update = make_update("/spread", chat_id="99999")
        self.listener._handle_update(update)

        self.listener._cmd_spread.assert_not_called()
        self.listener._send_reply.assert_not_called()

    def test_non_command_is_ignored(self):
        """非指令訊息（不以 / 開頭）應忽略。"""
        update = make_update("hello", chat_id="12345")
        self.listener._handle_update(update)

        self.listener._cmd_spread.assert_not_called()

    def test_spread_command_dispatched(self):
        update = make_update("/spread")
        self.listener._handle_update(update)
        self.listener._cmd_spread.assert_called_once()

    def test_status_command_dispatched(self):
        update = make_update("/status")
        self.listener._handle_update(update)
        self.listener._cmd_status.assert_called_once()

    def test_history_command_dispatched(self):
        update = make_update("/history")
        self.listener._handle_update(update)
        self.listener._cmd_history.assert_called_once()

    def test_unknown_command_sends_help(self):
        """未知指令應回傳說明訊息。"""
        update = make_update("/unknown")
        self.listener._handle_update(update)

        self.listener._send_reply.assert_called_once()
        msg = self.listener._send_reply.call_args[0][0]
        self.assertIn("/spread", msg)
        self.assertIn("/status", msg)
        self.assertIn("/history", msg)

    def test_command_with_bot_suffix_dispatched(self):
        """群組中的 /spread@MyBot 也應正確 dispatch。"""
        update = make_update("/spread@MyMonitorBot")
        self.listener._handle_update(update)
        self.listener._cmd_spread.assert_called_once()

    def test_empty_message_is_ignored(self):
        """無 message key 的 update 應安全忽略。"""
        self.listener._handle_update({"update_id": 1})
        self.listener._cmd_spread.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 3. _cmd_spread 測試
# ══════════════════════════════════════════════════════════════════════════════

class TestCmdSpread(unittest.TestCase):

    def setUp(self):
        self.listener = make_listener()
        self.listener._send_reply = MagicMock()

    @patch("src.notification.telegram_listener.requests.get")
    @patch("src.notification.telegram_listener.requests.post")
    def test_spread_success_shows_prices(self, mock_post, mock_get):
        """fetch 成功時，回覆訊息應包含三個價格欄位。"""
        with patch("src.data.bank_of_taiwan.fetch_usd_sell", return_value=32.82), \
             patch("src.data.max_exchange.fetch_usdt_twd_sell", return_value=32.95), \
             patch("src.engine.spread.calculate", return_value=0.13):
            self.listener._cmd_spread()

        msg = self.listener._send_reply.call_args[0][0]
        self.assertIn("32.9500", msg)
        self.assertIn("32.8200", msg)
        self.assertIn("0.1300", msg)

    def test_spread_fetch_error_sends_error_message(self):
        """fetch 失敗時，應回傳錯誤訊息而非靜默失敗。"""
        with patch("src.data.bank_of_taiwan.fetch_usd_sell",
                   side_effect=Exception("Network timeout")):
            self.listener._cmd_spread()

        msg = self.listener._send_reply.call_args[0][0]
        self.assertIn("失敗", msg)
        self.assertIn("Network timeout", msg)

    def test_spread_positive_shows_plus_sign(self):
        """正價差應顯示 + 號。"""
        with patch("src.data.bank_of_taiwan.fetch_usd_sell", return_value=32.80), \
             patch("src.data.max_exchange.fetch_usdt_twd_sell", return_value=33.00), \
             patch("src.engine.spread.calculate", return_value=0.20):
            self.listener._cmd_spread()

        msg = self.listener._send_reply.call_args[0][0]
        self.assertIn("+0.2000", msg)


# ══════════════════════════════════════════════════════════════════════════════
# 4. _cmd_status 測試
# ══════════════════════════════════════════════════════════════════════════════

class TestCmdStatus(unittest.TestCase):

    def test_status_no_data_yet(self):
        """尚無任何 cycle 資料時，應顯示「尚無資料」。"""
        listener = make_listener()
        listener._send_reply = MagicMock()
        listener._cmd_status()

        msg = listener._send_reply.call_args[0][0]
        self.assertIn("尚無資料", msg)

    def test_status_healthy(self):
        """consecutive_errors=0 時應顯示「正常」和 ✅。"""
        ss = {
            "last_bank_sell":            32.82,
            "last_max_sell":             32.95,
            "last_spread":               0.13,
            "last_update_time":          datetime.now(),
            "consecutive_errors":        0,
            "notified_thresholds_today": [],
        }
        listener = make_listener(shared_state=ss)
        listener._send_reply = MagicMock()
        listener._cmd_status()

        msg = listener._send_reply.call_args[0][0]
        self.assertIn("正常", msg)
        self.assertIn("✅", msg)

    def test_status_warning_1_2_errors(self):
        """1-2 次錯誤應顯示 ⚠️。"""
        ss = {
            "last_bank_sell":            32.82,
            "last_max_sell":             32.95,
            "last_spread":               0.13,
            "last_update_time":          datetime.now(),
            "consecutive_errors":        2,
            "notified_thresholds_today": [],
        }
        listener = make_listener(shared_state=ss)
        listener._send_reply = MagicMock()
        listener._cmd_status()

        msg = listener._send_reply.call_args[0][0]
        self.assertIn("⚠️", msg)

    def test_status_critical_3_plus_errors(self):
        """3+ 次錯誤應顯示 🔴。"""
        ss = {
            "last_bank_sell":            None,
            "last_max_sell":             None,
            "last_spread":               None,
            "last_update_time":          None,
            "consecutive_errors":        5,
            "notified_thresholds_today": [],
        }
        listener = make_listener(shared_state=ss)
        listener._send_reply = MagicMock()
        listener._cmd_status()

        msg = listener._send_reply.call_args[0][0]
        self.assertIn("🔴", msg)
        self.assertIn("5", msg)


# ══════════════════════════════════════════════════════════════════════════════
# 5. _cmd_history 測試
# ══════════════════════════════════════════════════════════════════════════════

class TestCmdHistory(unittest.TestCase):

    def test_history_no_thresholds(self):
        """今日無觸發門檻時，應顯示對應訊息。"""
        listener = make_listener()
        listener._send_reply = MagicMock()
        listener._cmd_history()

        msg = listener._send_reply.call_args[0][0]
        self.assertIn("尚未觸發", msg)

    def test_history_with_thresholds_shows_all(self):
        """有門檻時應顯示正確數量和數值。"""
        ss = {
            "last_bank_sell": None, "last_max_sell": None,
            "last_spread": None, "last_update_time": None,
            "consecutive_errors": 0,
            "notified_thresholds_today": [0.1, 0.2, 0.3],
        }
        listener = make_listener(shared_state=ss)
        listener._send_reply = MagicMock()
        listener._cmd_history()

        msg = listener._send_reply.call_args[0][0]
        self.assertIn("3", msg)
        self.assertIn("+0.10", msg)
        self.assertIn("+0.20", msg)
        self.assertIn("+0.30", msg)

    def test_history_sorted_descending(self):
        """門檻應由高到低排序。"""
        ss = {
            "last_bank_sell": None, "last_max_sell": None,
            "last_spread": None, "last_update_time": None,
            "consecutive_errors": 0,
            "notified_thresholds_today": [0.1, 0.3, 0.2],
        }
        listener = make_listener(shared_state=ss)
        listener._send_reply = MagicMock()
        listener._cmd_history()

        msg = listener._send_reply.call_args[0][0]
        pos_03 = msg.index("+0.30")
        pos_02 = msg.index("+0.20")
        pos_01 = msg.index("+0.10")
        self.assertLess(pos_03, pos_02)
        self.assertLess(pos_02, pos_01)


# ══════════════════════════════════════════════════════════════════════════════
# 6. start / stop 測試
# ══════════════════════════════════════════════════════════════════════════════

class TestStartStop(unittest.TestCase):

    def test_disabled_does_not_start_thread(self):
        """telegram.enabled=false 時不應啟動執行緒。"""
        tg = {"bot_token": "FAKE", "chat_id": "12345", "enabled": False}
        listener = make_listener(tg_config=tg)
        listener.start()
        self.assertFalse(listener._thread.is_alive())

    def test_stop_sets_event(self):
        """stop() 應設定 _stop_event（讓 _run() 能退出）。"""
        listener = make_listener()
        listener.stop(timeout=0.1)
        self.assertTrue(listener._stop_event.is_set())


# ══════════════════════════════════════════════════════════════════════════════
# 7. 輔助函數測試
# ══════════════════════════════════════════════════════════════════════════════

class TestHelpers(unittest.TestCase):

    def test_escape_html_ampersand(self):
        self.assertEqual(_escape_html("A & B"), "A &amp; B")

    def test_escape_html_lt_gt(self):
        self.assertEqual(_escape_html("<tag>"), "&lt;tag&gt;")

    def test_format_elapsed_seconds(self):
        self.assertEqual(_format_elapsed(45), "45 秒前")

    def test_format_elapsed_minutes(self):
        self.assertEqual(_format_elapsed(125), "2 分 5 秒前")

    def test_format_elapsed_hours(self):
        self.assertEqual(_format_elapsed(3661), "1 小時 1 分前")


# ══════════════════════════════════════════════════════════════════════════════
# 執行
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)

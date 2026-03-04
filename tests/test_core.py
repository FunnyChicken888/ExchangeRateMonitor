"""
核心邏輯單元測試（不需要外部服務）
測試範圍：
  1. spread.py     — 價差計算
  2. threshold.py  — 門檻交叉偵測 + 去重過濾
  3. state/manager.py — 狀態載入/儲存/跨日重置
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

# 確保 src 可被 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engine.spread    import calculate as calc_spread
from src.engine.threshold import get_crossed_thresholds, filter_new_events
from src.state.manager    import StateManager


# ══════════════════════════════════════════════════════════════════════════════
# 1. 價差計算測試
# ══════════════════════════════════════════════════════════════════════════════

class TestSpread(unittest.TestCase):

    def test_positive_spread(self):
        """MAX 比銀行貴 → 正價差"""
        result = calc_spread(max_sell=31.85, bank_sell=31.55)
        self.assertAlmostEqual(result, 0.30, places=4)

    def test_negative_spread(self):
        """MAX 比銀行便宜 → 負價差（套利機會）"""
        result = calc_spread(max_sell=31.40, bank_sell=31.55)
        self.assertAlmostEqual(result, -0.15, places=4)

    def test_zero_spread(self):
        """兩者相同 → 零價差"""
        result = calc_spread(max_sell=31.55, bank_sell=31.55)
        self.assertAlmostEqual(result, 0.0, places=4)

    def test_rounding_to_4_decimals(self):
        """結果應四捨五入到小數點後 4 位"""
        result = calc_spread(max_sell=31.123456789, bank_sell=31.0)
        self.assertEqual(result, round(31.123456789 - 31.0, 4))

    def test_invalid_type_raises(self):
        """非數字輸入應拋出 TypeError"""
        with self.assertRaises(TypeError):
            calc_spread("31.85", 31.55)

    def test_zero_value_raises(self):
        """零值輸入應拋出 ValueError"""
        with self.assertRaises(ValueError):
            calc_spread(0, 31.55)

    def test_negative_value_raises(self):
        """負值輸入應拋出 ValueError"""
        with self.assertRaises(ValueError):
            calc_spread(-1.0, 31.55)


# ══════════════════════════════════════════════════════════════════════════════
# 2. 門檻交叉偵測測試
# ══════════════════════════════════════════════════════════════════════════════

class TestThresholdCrossing(unittest.TestCase):

    # ── 上穿測試 ──────────────────────────────────────────────────────────────

    def test_single_up_cross(self):
        """0.15 → 0.25：只穿越 0.2"""
        events = get_crossed_thresholds(0.15, 0.25)
        self.assertEqual(events, [(0.2, "up")])

    def test_multiple_up_cross_skipped_levels(self):
        """0.15 → 0.38：跳級，穿越 0.2 和 0.3"""
        events = get_crossed_thresholds(0.15, 0.38)
        self.assertEqual(events, [(0.2, "up"), (0.3, "up")])

    def test_up_cross_exact_threshold(self):
        """0.15 → 0.30：精確落在門檻上，應包含 0.3"""
        events = get_crossed_thresholds(0.15, 0.30)
        self.assertIn((0.3, "up"), events)

    def test_up_cross_from_negative(self):
        """-0.05 → 0.15：穿越 0.0 和 0.1"""
        events = get_crossed_thresholds(-0.05, 0.15)
        self.assertIn((0.0, "up"), events)
        self.assertIn((0.1, "up"), events)

    # ── 下穿測試 ──────────────────────────────────────────────────────────────

    def test_single_down_cross(self):
        """0.25 → 0.15：只穿越 0.2"""
        events = get_crossed_thresholds(0.25, 0.15)
        self.assertEqual(events, [(0.2, "down")])

    def test_multiple_down_cross_skipped_levels(self):
        """0.38 → 0.12：跳級，穿越 0.3 和 0.2"""
        events = get_crossed_thresholds(0.38, 0.12)
        self.assertEqual(events, [(0.3, "down"), (0.2, "down")])

    def test_down_cross_to_negative(self):
        """0.15 → -0.05：穿越 0.1 和 0.0"""
        events = get_crossed_thresholds(0.15, -0.05)
        self.assertIn((0.1, "down"), events)
        self.assertIn((0.0, "down"), events)

    # ── 無穿越測試 ────────────────────────────────────────────────────────────

    def test_no_crossing_small_move(self):
        """0.25 → 0.26：未穿越任何門檻"""
        events = get_crossed_thresholds(0.25, 0.26)
        self.assertEqual(events, [])

    def test_no_crossing_unchanged(self):
        """價差不變：無事件"""
        events = get_crossed_thresholds(0.30, 0.30)
        self.assertEqual(events, [])

    def test_first_run_no_prev(self):
        """首次執行（prev=None）：無事件"""
        events = get_crossed_thresholds(None, 0.30)
        self.assertEqual(events, [])

    # ── 方向驗證 ──────────────────────────────────────────────────────────────

    def test_all_events_have_correct_direction_up(self):
        """上穿事件的 direction 全部為 'up'"""
        events = get_crossed_thresholds(0.0, 0.5)
        for _, direction in events:
            self.assertEqual(direction, "up")

    def test_all_events_have_correct_direction_down(self):
        """下穿事件的 direction 全部為 'down'"""
        events = get_crossed_thresholds(0.5, 0.0)
        for _, direction in events:
            self.assertEqual(direction, "down")

    # ── 自訂 step 測試 ────────────────────────────────────────────────────────

    def test_custom_step_0_5(self):
        """step=0.5：0.1 → 1.2 應穿越 0.5 和 1.0"""
        events = get_crossed_thresholds(0.1, 1.2, step=0.5)
        thresholds = [t for t, _ in events]
        self.assertIn(0.5, thresholds)
        self.assertIn(1.0, thresholds)
        self.assertNotIn(1.5, thresholds)


# ══════════════════════════════════════════════════════════════════════════════
# 3. 去重過濾測試
# ══════════════════════════════════════════════════════════════════════════════

class TestFilterNewEvents(unittest.TestCase):

    def test_filter_already_notified(self):
        """已通知的門檻應被過濾掉"""
        crossed = [(0.2, "up"), (0.3, "up"), (0.4, "up")]
        notified = [0.2, 0.3]
        result = filter_new_events(crossed, notified)
        self.assertEqual(result, [(0.4, "up")])

    def test_all_new(self):
        """全部都是新事件，不過濾"""
        crossed = [(0.2, "up"), (0.3, "up")]
        result = filter_new_events(crossed, [])
        self.assertEqual(result, crossed)

    def test_all_already_notified(self):
        """全部已通知，回傳空列表"""
        crossed = [(0.2, "up"), (0.3, "up")]
        result = filter_new_events(crossed, [0.2, 0.3])
        self.assertEqual(result, [])

    def test_empty_crossed(self):
        """無穿越事件，回傳空列表"""
        result = filter_new_events([], [0.2, 0.3])
        self.assertEqual(result, [])


# ══════════════════════════════════════════════════════════════════════════════
# 4. 狀態管理測試
# ══════════════════════════════════════════════════════════════════════════════

class TestStateManager(unittest.TestCase):

    def setUp(self):
        """每個測試使用獨立的暫存檔案"""
        self.tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w", encoding="utf-8"
        )
        self.tmp.write("{}")
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        """清理暫存檔案"""
        for f in [self.path, self.path + ".tmp"]:
            if os.path.exists(f):
                os.remove(f)

    def test_fresh_load_defaults(self):
        """全新狀態：prev_spread=None，notified_today=[]"""
        sm = StateManager(self.path)
        sm.load()
        self.assertIsNone(sm.prev_spread)
        self.assertEqual(sm.notified_thresholds_today, [])

    def test_save_and_reload(self):
        """儲存後重新載入，數值應一致"""
        sm = StateManager(self.path)
        sm.load()
        sm.update_spread(0.35)
        sm.mark_threshold_notified(0.3)
        sm.save()

        sm2 = StateManager(self.path)
        sm2.load()
        self.assertAlmostEqual(sm2.prev_spread, 0.35, places=4)
        self.assertIn(0.3, sm2.notified_thresholds_today)

    def test_date_rollover_resets_thresholds(self):
        """跨日後，notified_thresholds_today 應自動清空"""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        state_data = {
            "prev_spread": 0.35,
            "date": yesterday,
            "notified_thresholds_today": [0.1, 0.2, 0.3],
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(state_data, f)

        sm = StateManager(self.path)
        sm.load()

        # 跨日後應清空
        self.assertEqual(sm.notified_thresholds_today, [])
        # 但 prev_spread 應保留
        self.assertAlmostEqual(sm.prev_spread, 0.35, places=4)
        # 日期應更新為今天
        self.assertEqual(sm.current_date, date.today().isoformat())

    def test_same_day_no_reset(self):
        """同一天，notified_thresholds_today 不應被清空"""
        today = date.today().isoformat()
        state_data = {
            "prev_spread": 0.20,
            "date": today,
            "notified_thresholds_today": [0.1, 0.2],
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(state_data, f)

        sm = StateManager(self.path)
        sm.load()
        self.assertEqual(len(sm.notified_thresholds_today), 2)

    def test_no_duplicate_threshold_marks(self):
        """同一門檻不應重複加入 notified_today"""
        sm = StateManager(self.path)
        sm.load()
        sm.mark_threshold_notified(0.3)
        sm.mark_threshold_notified(0.3)
        sm.mark_threshold_notified(0.3)
        self.assertEqual(sm.notified_thresholds_today.count(0.3), 1)

    def test_missing_file_uses_defaults(self):
        """state.json 不存在時，應使用預設值"""
        missing_path = self.path + "_missing.json"
        sm = StateManager(missing_path)
        sm.load()
        self.assertIsNone(sm.prev_spread)
        self.assertEqual(sm.notified_thresholds_today, [])

    def test_corrupt_file_uses_defaults(self):
        """state.json 損毀時，應使用預設值（不崩潰）"""
        with open(self.path, "w") as f:
            f.write("{ invalid json !!!")
        sm = StateManager(self.path)
        sm.load()
        self.assertIsNone(sm.prev_spread)

    def test_atomic_save(self):
        """儲存應為原子操作（不留下 .tmp 檔案）"""
        sm = StateManager(self.path)
        sm.load()
        sm.update_spread(0.5)
        sm.save()
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        self.assertTrue(os.path.exists(self.path))


# ══════════════════════════════════════════════════════════════════════════════
# 執行
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)

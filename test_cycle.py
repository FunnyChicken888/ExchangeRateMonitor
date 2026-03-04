"""
單次 cycle 測試 — 驗證完整流程（mock 模式）
不需要 Telegram / DB 連線
"""
import sys
import os
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_cycle")

# 載入 config
with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

from src.state.manager    import StateManager
from src.persistence.database import Database
from src.main             import run_cycle

# 使用暫時 state 檔
state = StateManager("state_test_tmp.json")
state.load()

# DB disabled in config
db = Database(config["database"])

print("=" * 55)
print("  Single Cycle Test (mock mode)")
print("=" * 55)

# ── Cycle 1: first run, prev_spread = None ─────────────────────────────────
logger.info("--- Cycle 1 (prev_spread=None) ---")
run_cycle(config, state, db, logger)
logger.info("After cycle 1: prev_spread=%.4f | notified=%s",
            state.prev_spread, state.notified_thresholds_today)

# ── Cycle 2: same spread, no new events (dedup) ────────────────────────────
logger.info("--- Cycle 2 (same spread, dedup check) ---")
run_cycle(config, state, db, logger)
logger.info("After cycle 2: prev_spread=%.4f | notified=%s",
            state.prev_spread, state.notified_thresholds_today)

# ── Cycle 3: simulate spread change ────────────────────────────────────────
logger.info("--- Cycle 3 (spread change: 0.35 → 0.45) ---")
config["mock"]["max_sell"] = 32.95   # spread becomes 0.45
run_cycle(config, state, db, logger)
logger.info("After cycle 3: prev_spread=%.4f | notified=%s",
            state.prev_spread, state.notified_thresholds_today)

# ── Show final state file ──────────────────────────────────────────────────
if os.path.exists("state_test_tmp.json"):
    with open("state_test_tmp.json", encoding="utf-8") as f:
        content = f.read()
    print("\nFinal state_test_tmp.json:")
    print(content)
    os.remove("state_test_tmp.json")

print("=" * 55)
print("  Test complete ✅")
print("=" * 55)

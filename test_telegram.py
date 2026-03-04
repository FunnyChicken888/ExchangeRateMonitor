"""
Telegram 通知測試腳本

測試三種通知：
  1. 啟動通知 (send_startup)
  2. 門檻上穿事件 (send_event - up)
  3. 門檻下穿事件 (send_event - down)
"""

import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)

# ── 載入 config ────────────────────────────────────────────────────────────────
try:
    with open("config.json", encoding="utf-8") as f:
        config = json.load(f)
except FileNotFoundError:
    print("❌ 找不到 config.json，請先複製 config.example.json 並填入 bot_token / chat_id")
    sys.exit(1)

tg_cfg = config.get("telegram", {})

if not tg_cfg.get("enabled", True):
    print("⚠️  config.json 中 telegram.enabled = false，請先改為 true 再測試")
    sys.exit(1)

if tg_cfg.get("bot_token", "").startswith("YOUR_"):
    print("❌ 請先在 config.json 填入真實的 bot_token 與 chat_id")
    sys.exit(1)

from src.notification.telegram import send_startup, send_event, send_error_alert

print("=" * 55)
print("  Telegram 通知測試")
print("=" * 55)
print(f"  Bot Token : ...{tg_cfg['bot_token'][-8:]}")
print(f"  Chat ID   : {tg_cfg['chat_id']}")
print("=" * 55)

# ── 測試 1：啟動通知 ───────────────────────────────────────────────────────────
print("\n[1/3] 發送啟動通知 (send_startup)...")
ok = send_startup(tg_cfg)
if ok:
    print("  ✅ 啟動通知發送成功")
else:
    print("  ❌ 啟動通知發送失敗")

# ── 測試 2：門檻上穿事件 ───────────────────────────────────────────────────────
print("\n[2/3] 發送門檻上穿事件 (send_event - up)...")
ok = send_event(
    config=tg_cfg,
    threshold=0.10,
    direction="up",
    bank_sell=31.6150,
    max_sell=31.7200,
    spread=0.1050,
)
if ok:
    print("  ✅ 上穿事件通知發送成功")
else:
    print("  ❌ 上穿事件通知發送失敗")

# ── 測試 3：門檻下穿事件 ───────────────────────────────────────────────────────
print("\n[3/3] 發送門檻下穿事件 (send_event - down)...")
ok = send_event(
    config=tg_cfg,
    threshold=0.10,
    direction="down",
    bank_sell=31.6150,
    max_sell=31.7050,
    spread=0.0900,
)
if ok:
    print("  ✅ 下穿事件通知發送成功")
else:
    print("  ❌ 下穿事件通知發送失敗")

print("\n" + "=" * 55)
print("  測試完成 — 請確認 Telegram 是否收到 3 則訊息")
print("=" * 55)

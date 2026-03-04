"""
快速測試腳本 — 驗證匯率抓取功能 + mock 模式

執行方式：
    python test_fetch.py

功能：
  - 若 config.json 中 mock.enabled = true，使用 mock 價格
  - 否則嘗試真實 API 抓取
  - 顯示計算後的價差與門檻偵測結果
"""

import json
import logging
import os
import sys

# 確保專案根目錄在 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 設定 INFO 日誌（避免 DEBUG 太雜）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_fetch")

# ── 載入 config ────────────────────────────────────────────────────────────────
CONFIG_PATH = "config.json"
if not os.path.exists(CONFIG_PATH):
    print("[ERROR] config.json 不存在，請先複製 config.example.json")
    sys.exit(1)

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

print("=" * 55)
print("  ExchangeRateMonitor — 匯率抓取測試")
print("=" * 55)

mock_cfg = config.get("mock", {})
is_mock  = mock_cfg.get("enabled", False)

if is_mock:
    print("\n  ⚙️  MOCK 模式啟用 (config.json → mock.enabled = true)")
    print("  真實 API 不會被呼叫。")

# ── 1. 取得 bank_sell ──────────────────────────────────────────────────────────
print("\n[1/4] 取得台灣銀行 USD 即期賣出匯率...")
bank_sell = None
if is_mock:
    bank_sell = float(mock_cfg["bank_sell"])
    print(f"  ✅ [MOCK] 台灣銀行 USD 賣出：{bank_sell:.4f} TWD")
else:
    try:
        from src.data.bank_of_taiwan import fetch_usd_sell
        bank_sell = fetch_usd_sell(config["bank_of_taiwan"])
        print(f"  ✅ 台灣銀行 USD 即期賣出：{bank_sell:.4f} TWD")
    except Exception as e:
        print(f"  ❌ 台灣銀行抓取失敗：{e}")

# ── 2. 取得 max_sell ───────────────────────────────────────────────────────────
print("\n[2/4] 取得 MAX USDT/TWD 賣出價格...")
max_sell = None
if is_mock:
    max_sell = float(mock_cfg["max_sell"])
    print(f"  ✅ [MOCK] MAX USDT/TWD 賣出：{max_sell:.4f} TWD")
else:
    try:
        from src.data.max_exchange import fetch_usdt_twd_sell
        max_sell = fetch_usdt_twd_sell(config["max_exchange"])
        print(f"  ✅ MAX USDT/TWD 賣出：{max_sell:.4f} TWD")
    except Exception as e:
        print(f"  ❌ MAX 抓取失敗：{e}")

# ── 3. 計算價差 ────────────────────────────────────────────────────────────────
print("\n[3/4] 計算價差...")
spread = None
if max_sell is not None and bank_sell is not None:
    from src.engine.spread import calculate
    spread = calculate(max_sell, bank_sell)
    sign   = "+" if spread >= 0 else ""
    print(f"  ✅ 價差 = MAX({max_sell:.4f}) - Bank({bank_sell:.4f}) = {sign}{spread:.4f} TWD")
    if spread > 0:
        print(f"  📈 MAX 比銀行貴 {spread:.4f} TWD（正價差）")
    elif spread < 0:
        print(f"  📉 MAX 比銀行便宜 {abs(spread):.4f} TWD（套利機會）")
    else:
        print(f"  ➡️  兩者相同（零價差）")
else:
    print("  ⚠️  無法計算（至少一個來源失敗）")

# ── 4. 門檻偵測模擬 ────────────────────────────────────────────────────────────
print("\n[4/4] 門檻偵測模擬（假設 prev_spread = 0.20）...")
if spread is not None:
    from src.engine.threshold import get_crossed_thresholds, filter_new_events
    step        = float(config.get("spread_step", 0.1))
    prev_spread = 0.20
    crossed     = get_crossed_thresholds(prev_spread, spread, step)
    new_events  = filter_new_events(crossed, [])

    print(f"  prev_spread = {prev_spread:.4f}  →  curr_spread = {spread:.4f}")
    if new_events:
        for threshold, direction in new_events:
            arrow = "↑" if direction == "up" else "↓"
            print(f"  🔔 門檻 {arrow} {threshold:.4f} ({direction})")
    else:
        print("  ➡️  無門檻穿越事件")
else:
    print("  ⚠️  跳過（無有效價差）")

print("\n" + "=" * 55)
if is_mock:
    print("  測試完成 [MOCK 模式]")
    print("  部署到 NAS 後，將 mock.enabled 設為 false 即可使用真實 API。")
else:
    print("  測試完成")
print("=" * 55)

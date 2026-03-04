# ExchangeRateMonitor — Build TODO

## ✅ 全部完成

### 📁 Project Scaffolding
- [x] `TODO.md`
- [x] `requirements.txt`
- [x] `config.example.json`
- [x] `.gitignore`
- [x] `README.md`

### 🐍 Source Modules
- [x] `src/__init__.py`
- [x] `src/data/__init__.py`
- [x] `src/data/bank_of_taiwan.py` — 台灣銀行 USD 即期賣出匯率（HTML table 爬蟲）
- [x] `src/data/max_exchange.py`   — MAX USDT/TWD 賣出（公開 API + CoinGecko 備援）
- [x] `src/engine/__init__.py`
- [x] `src/engine/spread.py`       — spread = max_sell - bank_sell
- [x] `src/engine/threshold.py`    — 門檻交叉偵測 + 日內去重
- [x] `src/notification/__init__.py`
- [x] `src/notification/telegram.py` — Telegram Bot 通知
- [x] `src/persistence/__init__.py`
- [x] `src/persistence/database.py`  — MariaDB 事件儲存
- [x] `src/state/__init__.py`
- [x] `src/state/manager.py`         — state.json 載入/儲存/跨日重置
- [x] `src/main.py`                  — 主監測迴圈

### 🐳 Docker
- [x] `Dockerfile`
- [x] `docker-compose.yml`

### 🧪 Tests
- [x] `tests/test_core.py`  — 32/32 PASSED
- [x] `test_fetch.py`       — 真實 API 測試通過

---

## 📊 資料來源狀態

| 來源 | 狀態 | 備註 |
|------|------|------|
| 台灣銀行 `rate.bot.com.tw` | ✅ 正常 | 純 HTML table，即期賣出 31.1650 |
| MAX Exchange API | ⚠️ 本機 403 | NAS 上預期正常（Cloudflare 封鎖本機 IP）|
| CoinGecko 備援 | ✅ 正常 | MAX 失敗時自動切換，31.5900 |

---

## 🚀 部署步驟（NAS）

1. 複製 `config.example.json` → `config.json`
2. 填入 Telegram bot_token、chat_id
3. 填入 MariaDB 連線資訊
4. 確認 `mock.enabled = false`
5. `docker-compose up -d --build`

---

## 📝 設計變更記錄

- **2025** 原計畫使用 NextBank（將來銀行）作為銀行匯率來源
  - NextBank 網站為 React SPA，無法直接爬蟲
  - 改用**台灣銀行** `https://rate.bot.com.tw/xrt?Lang=zh-TW`
  - 純 HTML table，穩定可靠，官方參考匯率

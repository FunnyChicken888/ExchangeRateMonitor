# 📊 ExchangeRateMonitor

24/7 Docker-based spread monitoring system for **MAX USDT/TWD** vs **NextBank USD sell rate**.

```
spread = MAX USDT/TWD sell − NextBank USD sell
```

Sends Telegram alerts when the spread crosses configurable threshold levels,
and stores all crossing events in a MariaDB database for historical analysis.

---

## 🏗 Architecture

```
[Data Layer]          [Spread Engine]     [Threshold Engine]
nextbank.py      →    spread.py       →   threshold.py
max_exchange.py  ↗                        ↓
                                      [Notification]   [Persistence]
                                      telegram.py  →   database.py
                                                        ↓
                                                   [State Manager]
                                                   state/manager.py
```

| Module | File | Responsibility |
|--------|------|----------------|
| Data Layer | `src/data/nextbank.py` | Fetch NextBank USD sell rate |
| Data Layer | `src/data/max_exchange.py` | Fetch MAX USDT/TWD sell via public API |
| Spread Engine | `src/engine/spread.py` | `spread = max_sell - bank_sell` |
| Threshold Engine | `src/engine/threshold.py` | Detect up/down crossings, handle skipped levels |
| Notification | `src/notification/telegram.py` | Send Telegram Bot messages |
| Persistence | `src/persistence/database.py` | Store events in MariaDB |
| State | `src/state/manager.py` | Manage `state.json` with daily dedup reset |
| Orchestrator | `src/main.py` | Main loop, error handling, graceful shutdown |

---

## 🚀 Quick Start

### 1. Clone & configure

```bash
git clone <repo-url>
cd ExchangeRateMonitor

# Create your config from the template
cp config.example.json config.json
```

Edit `config.json` with your values:

```json
{
  "interval_seconds": 60,
  "spread_step": 0.1,
  "nextbank": {
    "url": "https://www.nextbank.com.tw/api/v1/exchange-rates",
    "currency": "USD",
    "rate_type": "sell"
  },
  "max_exchange": {
    "url": "https://max.maicoin.com/api/v2/tickers/usdttwd",
    "side": "sell"
  },
  "telegram": {
    "bot_token": "YOUR_BOT_TOKEN",
    "chat_id": "YOUR_CHAT_ID",
    "enabled": true
  },
  "database": {
    "host": "YOUR_NAS_IP",
    "port": 3306,
    "user": "YOUR_DB_USER",
    "password": "YOUR_DB_PASSWORD",
    "database": "exchange_monitor",
    "enabled": true
  },
  "logging": {
    "level": "INFO",
    "file": "logs/monitor.log",
    "max_bytes": 10485760,
    "backup_count": 5
  }
}
```

### 2. Create initial state file

```bash
echo '{}' > state.json
```

### 3. Build & run with Docker Compose

```bash
docker-compose up -d --build
```

### 4. Check logs

```bash
# Docker logs
docker logs -f exchange_rate_monitor

# Application log file
tail -f logs/monitor.log
```

---

## ⚙️ Configuration Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `interval_seconds` | int | `60` | How often to check prices (seconds) |
| `spread_step` | float | `0.1` | Threshold grid spacing (TWD) |
| `nextbank.url` | string | — | NextBank exchange rate endpoint |
| `nextbank.currency` | string | `"USD"` | Currency to monitor |
| `nextbank.rate_type` | string | `"sell"` | `"sell"` or `"buy"` |
| `max_exchange.url` | string | — | MAX API ticker URL |
| `max_exchange.side` | string | `"sell"` | `"sell"` or `"buy"` |
| `telegram.bot_token` | string | — | Telegram Bot API token |
| `telegram.chat_id` | string | — | Target chat/channel ID |
| `telegram.enabled` | bool | `true` | Toggle notifications |
| `database.host` | string | — | MariaDB host (NAS IP) |
| `database.port` | int | `3306` | MariaDB port |
| `database.user` | string | — | DB username |
| `database.password` | string | — | DB password |
| `database.database` | string | — | DB schema name |
| `database.enabled` | bool | `true` | Toggle DB persistence |
| `logging.level` | string | `"INFO"` | Log level (`DEBUG`/`INFO`/`WARNING`) |
| `logging.file` | string | `"logs/monitor.log"` | Log file path |

---

## 🗄 Database Schema

The table is **auto-created** on first run:

```sql
CREATE TABLE spread_events (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    event_time  DATETIME      NOT NULL,
    bank_sell   DECIMAL(10,4) NOT NULL,
    max_sell    DECIMAL(10,4) NOT NULL,
    spread      DECIMAL(10,4) NOT NULL,
    threshold   DECIMAL(10,4) NOT NULL,
    direction   ENUM('up','down') NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_event_time (event_time),
    INDEX idx_threshold  (threshold),
    INDEX idx_direction  (direction)
);
```

### Useful queries

```sql
-- Today's events
SELECT * FROM spread_events
WHERE DATE(event_time) = CURDATE()
ORDER BY event_time DESC;

-- Highest spread reached per day
SELECT DATE(event_time) AS day, MAX(spread) AS max_spread
FROM spread_events
GROUP BY day ORDER BY day DESC;

-- Threshold crossing frequency
SELECT threshold, direction, COUNT(*) AS hits
FROM spread_events
GROUP BY threshold, direction
ORDER BY threshold, direction;

-- Events in the last 7 days
SELECT * FROM spread_events
WHERE event_time >= NOW() - INTERVAL 7 DAY
ORDER BY event_time DESC;
```

---

## 🔄 State File

`state.json` is managed automatically. Example:

```json
{
  "prev_spread": 0.35,
  "date": "2025-01-15",
  "notified_thresholds_today": [0.1, 0.2, 0.3]
}
```

- **`prev_spread`**: Last known spread — used for crossing detection.
- **`date`**: Today's date — triggers dedup reset on new day.
- **`notified_thresholds_today`**: Thresholds already notified today — prevents repeat alerts.

> The state file survives container restarts. The DB is for history; state.json is for live operation.

---

## 📱 Telegram Notification Format

```
📈 價差上穿 +0.30 TWD

MAX  賣出：31.8500 TWD
銀行 賣出：31.5500 TWD
當前價差：+0.3000 TWD

🕐 2025-01-15 14:32:05
```

---

## 🧠 Threshold Crossing Logic

Given `step = 0.1`:

| prev_spread | curr_spread | Events fired |
|-------------|-------------|--------------|
| `0.15` | `0.38` | `(0.2, up)`, `(0.3, up)` |
| `0.38` | `0.12` | `(0.3, down)`, `(0.2, down)` |
| `0.25` | `0.26` | *(none — no threshold crossed)* |
| `-0.05` | `0.15` | `(0.0, up)`, `(0.1, up)` |

**Daily deduplication**: Each `(threshold, direction)` pair fires **at most once per day**.
The set resets automatically at midnight.

---

## 🐳 Docker Management

```bash
# Start
docker-compose up -d

# Stop
docker-compose down

# Rebuild after code changes
docker-compose up -d --build

# View real-time logs
docker logs -f exchange_rate_monitor

# Check container status
docker ps | grep exchange_rate_monitor

# Restart
docker-compose restart exchange-monitor
```

---

## 🔧 Troubleshooting

### NextBank rate fetch fails
- Check the `nextbank.url` in `config.json` — the endpoint may have changed.
- The scraper tries JSON first, then HTML fallback.
- Enable `DEBUG` logging to see the raw response.

### Database connection refused
- Verify `database.host` is the correct NAS IP.
- Ensure the MariaDB port is accessible from the Docker container.
- Set `"database": {"enabled": false}` to run without DB temporarily.

### No Telegram messages
- Verify `bot_token` and `chat_id` are correct.
- Test with: `curl "https://api.telegram.org/bot<TOKEN>/getMe"`
- Set `"telegram": {"enabled": false}` to disable notifications temporarily.

---

## 🚀 Future Extensions

- [ ] Multi-exchange comparison (Binance, OKX, etc.)
- [ ] Fee & slippage model for net profit estimation
- [ ] Web dashboard (Flask/FastAPI)
- [ ] Grafana + Prometheus metrics
- [ ] Funding rate monitoring
- [ ] Auto-order engine (Level 4)

---

## 📁 Project Structure

```
ExchangeRateMonitor/
├── src/
│   ├── data/
│   │   ├── nextbank.py          # NextBank USD sell rate fetcher
│   │   └── max_exchange.py      # MAX Exchange USDT/TWD API
│   ├── engine/
│   │   ├── spread.py            # Spread calculation
│   │   └── threshold.py         # Crossing detection + dedup
│   ├── notification/
│   │   └── telegram.py          # Telegram Bot notifications
│   ├── persistence/
│   │   └── database.py          # MariaDB event storage
│   ├── state/
│   │   └── manager.py           # state.json management
│   └── main.py                  # Main monitoring loop
├── config.example.json          # Config template (commit this)
├── config.json                  # Your config (gitignored)
├── state.json                   # Runtime state (gitignored)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md

"""
ExchangeRateMonitor — Main monitoring loop.

Orchestration flow (every interval_seconds):
  1. Fetch prices      -> NextBank USD sell + MAX USDT/TWD sell
  2. Calculate spread  -> max_sell - bank_sell
  3. Detect crossings  -> threshold engine
  4. Deduplicate       -> filter already-notified thresholds today
  5. For each new event:
       a. Send Telegram notification
       b. Insert event into MariaDB
       c. Mark threshold as notified in state
  6. Update prev_spread in state
  7. Save state to disk
  8. Sleep until next cycle
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import datetime

# ── Path fix: ensure project root is in sys.path ──────────────────────────────
# Needed when running directly as `python src/main.py`.
# Has no effect when running as `python -m src.main` (root already in path).
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# ── Module imports ─────────────────────────────────────────────────────────────
from src.data.bank_of_taiwan import fetch_usd_sell
from src.data.max_exchange   import fetch_usdt_twd_sell
from src.engine.spread     import calculate as calculate_spread
from src.engine.threshold  import get_crossed_thresholds, filter_new_events
from src.notification.telegram          import send_event, send_startup, send_error_alert
from src.notification.telegram_listener import TelegramCommandListener
from src.persistence.database  import Database
from src.state.manager         import StateManager

# ── Constants ──────────────────────────────────────────────────────────────────
CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.json")
STATE_PATH  = os.environ.get("STATE_PATH",  "state.json")

# ── Globals ────────────────────────────────────────────────────────────────────
_running = True   # set to False by SIGTERM/SIGINT for graceful shutdown


# ══════════════════════════════════════════════════════════════════════════════
# Setup
# ══════════════════════════════════════════════════════════════════════════════

def load_config(path: str) -> dict:
    """Load and validate config.json."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            "Config file not found: {}\n"
            "Copy config.example.json -> config.json and fill in your values.".format(path)
        )
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Basic validation
    required_top = ["interval_seconds", "spread_step", "bank_of_taiwan", "max_exchange",
                     "telegram", "database"]
    for key in required_top:
        if key not in cfg:
            raise KeyError("Missing required config key: '{}'".format(key))

    return cfg


def setup_logging(log_cfg: dict) -> None:
    """Configure root logger with console + rotating file handler."""
    level_name = log_cfg.get("level", "INFO").upper()
    level      = getattr(logging, level_name, logging.INFO)

    fmt       = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    datefmt   = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # Rotating file handler (optional)
    log_file = log_cfg.get("file")
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes    = log_cfg.get("max_bytes",    10 * 1024 * 1024),  # 10 MB
            backupCount = log_cfg.get("backup_count", 5),
            encoding    = "utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def _handle_signal(signum, frame) -> None:
    """Graceful shutdown on SIGTERM / SIGINT."""
    global _running
    logging.getLogger(__name__).info(
        "Signal %s received — shutting down gracefully...", signum
    )
    _running = False


# ══════════════════════════════════════════════════════════════════════════════
# Core monitoring cycle
# ══════════════════════════════════════════════════════════════════════════════

def run_cycle(
    config:       dict,
    state:        StateManager,
    db:           Database,
    logger:       logging.Logger,
    shared_state: dict | None = None,
) -> None:
    """
    Execute one monitoring cycle:
      fetch -> spread -> threshold -> dedup -> notify -> persist -> update state.
    """
    # ── 1. Fetch prices (or use mock values) ───────────────────────────────
    mock_cfg = config.get("mock", {})
    if mock_cfg.get("enabled", False):
        bank_sell = float(mock_cfg["bank_sell"])
        max_sell  = float(mock_cfg["max_sell"])
        logger.info("MOCK MODE — bank_sell=%.4f  max_sell=%.4f", bank_sell, max_sell)
    else:
        bank_sell = fetch_usd_sell(config["bank_of_taiwan"])
        max_sell  = fetch_usdt_twd_sell(config["max_exchange"])

    # ── 2. Calculate spread ────────────────────────────────────────────────
    spread = calculate_spread(max_sell, bank_sell)
    logger.info(
        "Prices — Bank: %.4f | MAX: %.4f | Spread: %+.4f",
        bank_sell, max_sell, spread,
    )

    # ── 3. Detect threshold crossings ──────────────────────────────────────
    prev_spread = state.prev_spread
    step        = float(config.get("spread_step", 0.1))
    crossed     = get_crossed_thresholds(prev_spread, spread, step)

    # ── 4. Deduplicate (same threshold, same day) ──────────────────────────
    new_events = filter_new_events(crossed, state.notified_thresholds_today)

    # ── 5. Process each new crossing event ────────────────────────────────
    for threshold, direction in new_events:
        logger.info(
            "EVENT: threshold=%.4f direction=%s spread=%.4f",
            threshold, direction, spread,
        )

        # a. Telegram notification
        send_event(
            config    = config["telegram"],
            threshold = threshold,
            direction = direction,
            bank_sell = bank_sell,
            max_sell  = max_sell,
            spread    = spread,
        )

        # b. Database persistence
        db.ping()   # reconnect if needed
        db.insert_event(
            bank_sell  = bank_sell,
            max_sell   = max_sell,
            spread     = spread,
            threshold  = threshold,
            direction  = direction,
            event_time = datetime.now(),
        )

        # c. Mark as notified in state
        state.mark_threshold_notified(threshold)

    # ── 6. Update prev_spread ──────────────────────────────────────────────
    state.update_spread(spread)

    # ── 7. Save state ──────────────────────────────────────────────────────
    state.save()

    # ── 8. Update shared_state for listener thread ─────────────────────────
    if shared_state is not None:
        shared_state["last_bank_sell"]            = bank_sell
        shared_state["last_max_sell"]             = max_sell
        shared_state["last_spread"]               = spread
        shared_state["last_update_time"]          = datetime.now()
        shared_state["notified_thresholds_today"] = list(state.notified_thresholds_today)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    global _running

    # ── Signal handlers ────────────────────────────────────────────────────
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)

    # ── Load config ────────────────────────────────────────────────────────
    try:
        config = load_config(CONFIG_PATH)
    except (FileNotFoundError, KeyError) as exc:
        print("[FATAL] Config error: {}".format(exc), file=sys.stderr)
        sys.exit(1)

    # ── Setup logging ──────────────────────────────────────────────────────
    setup_logging(config.get("logging", {}))
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("ExchangeRateMonitor starting up")
    logger.info("Config: %s", CONFIG_PATH)
    logger.info("State:  %s", STATE_PATH)
    logger.info("Interval: %ss | Step: %.2f TWD",
                config["interval_seconds"], config["spread_step"])
    logger.info("=" * 60)

    # ── Load state ─────────────────────────────────────────────────────────
    state = StateManager(STATE_PATH)
    state.load()
    logger.info(
        "State loaded — prev_spread=%s | notified_today=%d threshold(s)",
        state.prev_spread,
        len(state.notified_thresholds_today),
    )

    # ── Connect to database ────────────────────────────────────────────────
    db = Database(config["database"])
    try:
        db.connect()
    except Exception as exc:
        logger.error("DB connection failed: %s — continuing without DB.", exc)

    # ── Startup notification ───────────────────────────────────────────────
    send_startup(config["telegram"])

    # ── Shared state for listener thread ───────────────────────────────────
    shared_state: dict = {
        "last_bank_sell":            None,
        "last_max_sell":             None,
        "last_spread":               None,
        "last_update_time":          None,
        "consecutive_errors":        0,
        "notified_thresholds_today": list(state.notified_thresholds_today),
    }

    # ── Start Telegram command listener ────────────────────────────────────
    listener = TelegramCommandListener(
        tg_config    = config["telegram"],
        config       = config,
        shared_state = shared_state,
    )
    listener.start()

    # ── Main loop ──────────────────────────────────────────────────────────
    consecutive_errors     = 0
    max_consecutive_errors = 10

    while _running:
        cycle_start = time.monotonic()

        try:
            run_cycle(config, state, db, logger, shared_state)
            consecutive_errors = 0
            shared_state["consecutive_errors"] = 0

        except Exception as exc:
            consecutive_errors += 1
            shared_state["consecutive_errors"] = consecutive_errors
            logger.error(
                "Cycle error [%d/%d]: %s",
                consecutive_errors, max_consecutive_errors, exc,
                exc_info=True,
            )

            # Send alert after 3 consecutive failures
            if consecutive_errors == 3:
                send_error_alert(
                    config["telegram"],
                    "連續 {} 次監測失敗: {}".format(consecutive_errors, exc),
                )

            # Exit if too many consecutive errors (Docker will restart)
            if consecutive_errors >= max_consecutive_errors:
                logger.critical(
                    "Too many consecutive errors (%d) — exiting for restart.",
                    consecutive_errors,
                )
                db.close()
                sys.exit(1)

        # ── Sleep for the remainder of the interval ────────────────────────
        elapsed   = time.monotonic() - cycle_start
        sleep_for = max(0.0, float(config["interval_seconds"]) - elapsed)
        logger.debug("Cycle took %.2fs — sleeping %.2fs", elapsed, sleep_for)

        # Use short sleep slices so SIGTERM is handled promptly
        slept = 0.0
        while _running and slept < sleep_for:
            time.sleep(min(1.0, sleep_for - slept))
            slept += 1.0

    # ── Graceful shutdown ──────────────────────────────────────────────────
    logger.info("Shutting down — saving state and closing DB...")
    listener.stop()
    state.save()
    db.close()
    logger.info("ExchangeRateMonitor stopped.")


if __name__ == "__main__":
    main()

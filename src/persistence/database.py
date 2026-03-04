"""
MariaDB persistence layer.

Only stores threshold-crossing EVENTS — not every tick.
This keeps the database lean and query-fast.

Table schema (auto-created on first run):
  spread_events
  ├── id          INT AUTO_INCREMENT PK
  ├── event_time  DATETIME NOT NULL
  ├── bank_sell   DECIMAL(10,4) NOT NULL
  ├── max_sell    DECIMAL(10,4) NOT NULL
  ├── spread      DECIMAL(10,4) NOT NULL
  ├── threshold   DECIMAL(10,4) NOT NULL
  ├── direction   ENUM('up','down')
  └── created_at  TIMESTAMP DEFAULT NOW()
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Generator, Optional

import pymysql
import pymysql.cursors

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS spread_events (
    id          INT          NOT NULL AUTO_INCREMENT,
    event_time  DATETIME     NOT NULL,
    bank_sell   DECIMAL(10,4) NOT NULL,
    max_sell    DECIMAL(10,4) NOT NULL,
    spread      DECIMAL(10,4) NOT NULL,
    threshold   DECIMAL(10,4) NOT NULL,
    direction   ENUM('up','down') NOT NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_event_time  (event_time),
    INDEX idx_threshold   (threshold),
    INDEX idx_direction   (direction)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_INSERT_EVENT_SQL = """
INSERT INTO spread_events
    (event_time, bank_sell, max_sell, spread, threshold, direction)
VALUES
    (%s, %s, %s, %s, %s, %s)
"""


class Database:
    """
    Thin wrapper around a PyMySQL connection to the NAS MariaDB instance.

    Usage:
        db = Database(config["database"])
        db.connect()
        db.insert_event(...)
        db.close()

    Or use as a context manager:
        with Database(config["database"]) as db:
            db.insert_event(...)
    """

    def __init__(self, config: dict) -> None:
        self._config  = config
        self._conn    = None
        self._enabled = config.get("enabled", True)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Open the database connection and ensure the events table exists."""
        if not self._enabled:
            logger.debug("Database disabled — skipping connection.")
            return

        self._conn = pymysql.connect(
            host        = self._config["host"],
            port        = int(self._config.get("port", 3306)),
            user        = self._config["user"],
            password    = self._config["password"],
            database    = self._config["database"],
            charset     = "utf8mb4",
            cursorclass = pymysql.cursors.DictCursor,
            autocommit  = False,
            connect_timeout = 10,
        )
        logger.info(
            "Connected to MariaDB at %s:%s/%s",
            self._config["host"],
            self._config.get("port", 3306),
            self._config["database"],
        )
        self._ensure_table()

    def close(self) -> None:
        """Close the database connection gracefully."""
        if self._conn:
            try:
                self._conn.close()
                logger.debug("Database connection closed.")
            except Exception as exc:
                logger.warning("Error closing DB connection: %s", exc)
            finally:
                self._conn = None

    def __enter__(self) -> "Database":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ── Public API ─────────────────────────────────────────────────────────

    def insert_event(
        self,
        bank_sell:  float,
        max_sell:   float,
        spread:     float,
        threshold:  float,
        direction:  str,
        event_time: Optional[datetime] = None,
    ) -> Optional[int]:
        """
        Insert a threshold-crossing event into spread_events.

        Args:
            bank_sell:  NextBank USD sell rate.
            max_sell:   MAX USDT/TWD sell rate.
            spread:     Calculated spread (max_sell - bank_sell).
            threshold:  The threshold level that was crossed.
            direction:  "up" or "down".
            event_time: Timestamp of the event (defaults to now).

        Returns:
            The auto-incremented row ID, or None if DB is disabled.
        """
        if not self._enabled or self._conn is None:
            logger.debug("DB insert skipped (disabled or not connected).")
            return None

        if event_time is None:
            event_time = datetime.now()

        try:
            with self._cursor() as cur:
                cur.execute(
                    _INSERT_EVENT_SQL,
                    (event_time, bank_sell, max_sell, spread, threshold, direction),
                )
            self._conn.commit()
            row_id = cur.lastrowid
            logger.info(
                "DB event inserted: id=%s threshold=%.4f direction=%s spread=%.4f",
                row_id, threshold, direction, spread,
            )
            return row_id
        except pymysql.Error as exc:
            logger.error("DB insert failed: %s", exc)
            try:
                self._conn.rollback()
            except Exception:
                pass
            return None

    def ping(self) -> bool:
        """
        Check if the connection is alive; attempt reconnect if not.

        Returns:
            True if connection is healthy, False otherwise.
        """
        if not self._enabled:
            return True
        if self._conn is None:
            return False
        try:
            self._conn.ping(reconnect=True)
            return True
        except Exception as exc:
            logger.warning("DB ping failed: %s", exc)
            return False

    # ── Internal helpers ───────────────────────────────────────────────────

    def _ensure_table(self) -> None:
        """Create the spread_events table if it does not exist."""
        with self._cursor() as cur:
            cur.execute(_CREATE_TABLE_SQL)
        self._conn.commit()
        logger.debug("spread_events table verified/created.")

    @contextmanager
    def _cursor(self) -> Generator:
        """Yield a cursor and ensure it is closed afterwards."""
        cur = self._conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

"""
Runtime state manager.

Persists the monitoring state to state.json so that:
  - The threshold engine knows the previous spread value.
  - Daily deduplication survives process restarts within the same day.
  - A new day automatically resets the notified-thresholds set.

state.json schema:
  {
    "prev_spread": 0.3500,
    "date":        "2025-01-15",
    "notified_thresholds_today": [0.1, 0.2, 0.3]
  }

Design principles:
  - DB is for history.
  - state.json is for live operation.
  - They are independent; a DB failure never corrupts the state.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_STATE: Dict[str, Any] = {
    "prev_spread":               None,
    "date":                      None,
    "notified_thresholds_today": [],
}


class StateManager:
    """
    Load, update, and persist the monitoring state to/from state.json.

    Usage:
        sm = StateManager("state.json")
        state = sm.load()

        # After processing events:
        sm.mark_threshold_notified(0.3)
        sm.update_spread(0.35)
        sm.save()
    """

    def __init__(self, path: str = "state.json") -> None:
        self._path: str = path
        self._state: Dict[str, Any] = {}

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def load(self) -> Dict[str, Any]:
        """
        Load state from disk.

        If the file does not exist or is corrupt, returns a fresh default
        state.  Also performs a date-rollover check — if the stored date
        differs from today, the notified-thresholds list is cleared.

        Returns:
            The current state dict (also stored internally).
        """
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                # Merge with defaults to handle missing keys from old versions
                self._state = {**_DEFAULT_STATE, **loaded}
                logger.debug("State loaded from %s: %s", self._path, self._state)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "Cannot read state file (%s) — starting fresh: %s",
                    self._path, exc,
                )
                self._state = dict(_DEFAULT_STATE)
        else:
            logger.info("No state file found at %s — starting fresh.", self._path)
            self._state = dict(_DEFAULT_STATE)

        self._maybe_rollover()
        return self._state

    def save(self) -> None:
        """
        Persist the current state to disk.

        Attempts an atomic write (temp file + rename) first.
        Falls back to direct write if rename fails — this happens on
        Windows Docker Desktop with bind-mounted files (Errno 16).
        """
        tmp_path = self._path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self._path)
            logger.debug("State saved to %s (atomic)", self._path)
        except OSError as exc:
            # Atomic rename failed (common on Windows Docker bind mounts).
            # Clean up temp file and fall back to direct write.
            logger.debug(
                "Atomic save failed (%s) — falling back to direct write.", exc
            )
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            try:
                with open(self._path, "w", encoding="utf-8") as f:
                    json.dump(self._state, f, indent=2, ensure_ascii=False)
                logger.debug("State saved to %s (direct write)", self._path)
            except OSError as exc2:
                logger.error(
                    "Failed to save state to %s: %s", self._path, exc2
                )

    # ── Accessors ──────────────────────────────────────────────────────────

    @property
    def prev_spread(self) -> Optional[float]:
        """Previous spread value (None on first run)."""
        return self._state.get("prev_spread")

    @property
    def notified_thresholds_today(self) -> List[float]:
        """List of threshold values already notified today."""
        return self._state.get("notified_thresholds_today", [])

    @property
    def current_date(self) -> Optional[str]:
        """The date string stored in state (YYYY-MM-DD)."""
        return self._state.get("date")

    # ── Mutators ───────────────────────────────────────────────────────────

    def update_spread(self, spread: float) -> None:
        """
        Update the stored previous spread value.

        Args:
            spread: The spread value from the current cycle.
        """
        self._state["prev_spread"] = spread
        logger.debug("State: prev_spread updated to %.4f", spread)

    def mark_threshold_notified(self, threshold: float) -> None:
        """
        Add a threshold to today's notified set.

        Args:
            threshold: The threshold value that was just notified.
        """
        rounded = round(threshold, 8)
        if rounded not in self._state["notified_thresholds_today"]:
            self._state["notified_thresholds_today"].append(rounded)
            logger.debug("State: threshold %.4f marked as notified today.", rounded)

    # ── Internal helpers ───────────────────────────────────────────────────

    def _maybe_rollover(self) -> None:
        """
        If the stored date differs from today, reset the notified-thresholds
        list and update the date.  This is the daily deduplication reset.
        """
        today_str = date.today().isoformat()
        stored    = self._state.get("date")

        if stored != today_str:
            if stored is not None:
                logger.info(
                    "Date rollover: %s -> %s. Resetting notified thresholds.",
                    stored, today_str,
                )
            self._state["date"]                      = today_str
            self._state["notified_thresholds_today"] = []

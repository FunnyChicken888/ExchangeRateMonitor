"""
Threshold crossing detection engine.

Core logic
──────────
Given a previous spread value and a current spread value, this module
detects which threshold levels (multiples of `step`) were crossed and
in which direction.

Crossing definitions
────────────────────
  Up-cross   at T:  prev_spread < T  AND  curr_spread >= T
  Down-cross at T:  prev_spread >= T AND  curr_spread <  T

In both cases the set of crossed thresholds is:
    { T : T is a multiple of step, T ∈ (min(prev, curr), max(prev, curr)] }

Direction is determined by whether curr > prev (up) or curr < prev (down).

Skipped levels
──────────────
If the spread jumps by more than one step in a single interval, ALL
intermediate thresholds are returned — each as a separate event.

Daily deduplication
───────────────────
The caller (main loop / state manager) is responsible for filtering
already-notified thresholds.  This module only returns raw crossing events.

Example
───────
    prev = 0.15, curr = 0.38, step = 0.1
    → crossed = [(0.2, 'up'), (0.3, 'up')]   # 0.4 not included (0.38 < 0.4)

    prev = 0.38, curr = 0.12, step = 0.1
    → crossed = [(0.3, 'down'), (0.2, 'down')]
"""

import logging
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Type alias
CrossingEvent = Tuple[float, str]   # (threshold, direction)


def get_crossed_thresholds(
    prev_spread: float,
    curr_spread: float,
    step: float = 0.1,
) -> List[CrossingEvent]:
    """
    Return all threshold levels crossed between prev_spread and curr_spread.

    Args:
        prev_spread: Spread value from the previous cycle.
        curr_spread: Spread value from the current cycle.
        step:        Threshold grid spacing (default 0.1 TWD).

    Returns:
        List of (threshold, direction) tuples, ordered from the threshold
        nearest to prev_spread outward toward curr_spread.
        Returns an empty list if:
          - prev_spread is None (first run)
          - the spread did not change
          - no threshold was crossed
    """
    if prev_spread is None:
        logger.debug("First run — no previous spread, skipping threshold check.")
        return []

    if abs(curr_spread - prev_spread) < 1e-9:
        logger.debug("Spread unchanged (%.4f), no crossing.", curr_spread)
        return []

    # Use Decimal arithmetic to avoid floating-point accumulation errors
    step_d = Decimal(str(step))
    prev_d = Decimal(str(round(prev_spread, 8)))
    curr_d = Decimal(str(round(curr_spread, 8)))

    low_d  = min(prev_d, curr_d)
    high_d = max(prev_d, curr_d)
    direction = "up" if curr_spread > prev_spread else "down"

    # Find all multiples of step_d strictly inside (low_d, high_d]
    # First candidate: smallest multiple of step_d that is > low_d
    first = (low_d / step_d).to_integral_value(rounding=ROUND_FLOOR) * step_d + step_d

    crossed: List[CrossingEvent] = []
    t = first
    while t <= high_d:
        threshold_val = float(t)
        crossed.append((round(threshold_val, 8), direction))
        t += step_d

    # Order: nearest to prev_spread first
    if direction == "down":
        crossed.reverse()

    if crossed:
        logger.info(
            "Spread %.4f → %.4f | %d threshold(s) crossed (%s): %s",
            prev_spread,
            curr_spread,
            len(crossed),
            direction,
            [c[0] for c in crossed],
        )
    else:
        logger.debug(
            "Spread %.4f → %.4f | no threshold crossed (step=%.2f)",
            prev_spread, curr_spread, step,
        )

    return crossed


def filter_new_events(
    crossed: List[CrossingEvent],
    notified_today: List[float],
) -> List[CrossingEvent]:
    """
    Remove thresholds that have already been notified today.

    Args:
        crossed:        Raw list from get_crossed_thresholds().
        notified_today: List of threshold values already notified today.

    Returns:
        Filtered list containing only new (not-yet-notified) events.
    """
    notified_set = set(round(t, 8) for t in notified_today)
    new_events = [
        (t, d) for t, d in crossed
        if round(t, 8) not in notified_set
    ]

    skipped = len(crossed) - len(new_events)
    if skipped:
        logger.debug("Dedup: skipped %d already-notified threshold(s) today.", skipped)

    return new_events

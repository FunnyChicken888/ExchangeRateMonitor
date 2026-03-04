"""
Spread calculation engine.

Formula:
    spread = max_sell - bank_sell

Where:
    max_sell  — USDT/TWD sell price on MAX Exchange
    bank_sell — USD sell rate at NextBank (TWD per 1 USD)

Both values are treated as TWD-denominated prices for 1 unit of USD/USDT.
Since USDT ≈ 1 USD, the spread represents the TWD premium (or discount)
of buying USD via the bank vs. buying USDT on MAX.

A positive spread means MAX USDT is MORE expensive than the bank rate.
A negative spread means MAX USDT is CHEAPER than the bank rate (arbitrage opportunity).
"""

import logging

logger = logging.getLogger(__name__)


def calculate(max_sell: float, bank_sell: float) -> float:
    """
    Calculate the spread between MAX USDT/TWD sell and NextBank USD sell.

    Args:
        max_sell:  MAX Exchange USDT/TWD sell (ask) price.
        bank_sell: NextBank USD sell rate in TWD.

    Returns:
        float — spread value, rounded to 4 decimal places.

    Raises:
        TypeError: If either argument is not a number.
        ValueError: If either argument is non-positive.
    """
    if not isinstance(max_sell, (int, float)):
        raise TypeError(f"max_sell must be a number, got {type(max_sell)}")
    if not isinstance(bank_sell, (int, float)):
        raise TypeError(f"bank_sell must be a number, got {type(bank_sell)}")
    if max_sell <= 0:
        raise ValueError(f"max_sell must be positive, got {max_sell}")
    if bank_sell <= 0:
        raise ValueError(f"bank_sell must be positive, got {bank_sell}")

    spread = max_sell - bank_sell
    spread = round(spread, 4)

    logger.debug(
        "Spread calculation: %.4f (MAX) - %.4f (Bank) = %.4f",
        max_sell, bank_sell, spread,
    )
    return spread

"""
Bank of Taiwan (台灣銀行) USD sell rate fetcher.

Source: https://rate.bot.com.tw/xrt?Lang=zh-TW
Format: Plain HTML table (NOT a SPA) — reliable and easy to scrape.

Table column layout (0-indexed, confirmed by live scrape):
  0  幣別 (currency name + flag, merged cell, e.g. "美金 (USD)美金 (USD)")
  1  現金買入 (cash buy)
  2  現金賣出 (cash sell)
  3  即期買入 (spot buy)
  4  即期賣出 (spot sell)   ← we use this
  5  查詢 (link)
  6  查詢 (link)
  7  現金買入 (repeated — historical section)
  ...

We target the "即期賣出" (spot selling rate) for USD.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_TIMEOUT = 10

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer":         "https://rate.bot.com.tw/",
}

# Default URL — Bank of Taiwan exchange rate page (plain HTML, stable)
_DEFAULT_URL = "https://rate.bot.com.tw/xrt?Lang=zh-TW"

# Column index for each rate type (0-based, confirmed by live scrape 2025)
# Row structure: [0]=幣別  [1]=現金買入  [2]=現金賣出  [3]=即期買入  [4]=即期賣出  [5]=查詢 ...
_COL_CASH_BUY  = 1
_COL_CASH_SELL = 2
_COL_SPOT_BUY  = 3
_COL_SPOT_SELL = 4


def fetch_usd_sell(config: dict) -> float:
    """
    Fetch Bank of Taiwan USD spot sell rate (即期賣出).

    Args:
        config: The "bank_of_taiwan" section from config.json, e.g.:
            {
                "url":       "https://rate.bot.com.tw/xrt?Lang=zh-TW",
                "currency":  "USD",
                "rate_type": "spot_sell"   # cash_buy | cash_sell | spot_buy | spot_sell
            }

    Returns:
        float — USD sell rate in TWD.

    Raises:
        ValueError: If the rate cannot be parsed.
        requests.RequestException: On network errors.
    """
    url       = config.get("url",       _DEFAULT_URL)
    currency  = config.get("currency",  "USD").upper()
    rate_type = config.get("rate_type", "spot_sell").lower()

    col_idx = {
        "cash_buy":  _COL_CASH_BUY,
        "cash_sell": _COL_CASH_SELL,
        "spot_buy":  _COL_SPOT_BUY,
        "spot_sell": _COL_SPOT_SELL,
    }.get(rate_type, _COL_SPOT_SELL)

    logger.debug("Fetching Bank of Taiwan rates from %s", url)

    resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()

    rate = _parse_html(resp.text, currency, col_idx)
    if rate is not None:
        logger.info(
            "Bank of Taiwan %s %s = %.4f",
            currency, rate_type, rate,
        )
        return rate

    raise ValueError(
        f"Cannot find {currency} {rate_type} rate in Bank of Taiwan page.\n"
        f"URL: {url}\n"
        "Please verify the page structure has not changed."
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

def _parse_html(html: str, currency: str, col_idx: int) -> Optional[float]:
    """
    Parse the Bank of Taiwan exchange rate HTML table.

    Finds the row where the currency code (e.g. 'USD') appears,
    then extracts the value at the specified column index.
    """
    soup = BeautifulSoup(html, "lxml")

    # The main table has id="table_rate" or is the first large table
    table = (
        soup.find("table", {"id": "table_rate"})
        or soup.find("table")
    )
    if not table:
        logger.debug("No <table> found in Bank of Taiwan response")
        return None

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue

        # Check if this row contains the target currency code
        row_text = " ".join(c.get_text(strip=True) for c in cells)
        if currency.upper() not in row_text.upper():
            continue

        # Extract the value at the target column
        if col_idx < len(cells):
            raw = cells[col_idx].get_text(strip=True).replace(",", "")
            try:
                val = float(raw)
                if val > 0:
                    return val
            except ValueError:
                logger.debug(
                    "Cannot convert '%s' to float at col %d for %s",
                    raw, col_idx, currency,
                )

        # Fallback: scan all cells for a plausible TWD rate (20–55)
        logger.debug(
            "Col %d not usable for %s, scanning all cells...", col_idx, currency
        )
        for cell in cells:
            raw = cell.get_text(strip=True).replace(",", "")
            try:
                val = float(raw)
                if 20.0 <= val <= 55.0:
                    return val
            except ValueError:
                continue

    return None

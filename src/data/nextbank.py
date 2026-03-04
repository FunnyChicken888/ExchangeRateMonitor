"""
NextBank USD sell rate fetcher.

Tries multiple URL patterns in order (JSON API first, then HTML scraping).
The primary URL is configurable via config.json → "nextbank.url".

Fallback URL list (tried in order if primary fails):
  1. config["url"]                                    (user-configured)
  2. https://www.nextbank.com.tw/api/exchange-rates
  3. https://www.nextbank.com.tw/api/v2/exchange-rates
  4. https://www.nextbank.com.tw/exchange-rate        (HTML page)
  5. https://www.nextbank.com.tw/                     (homepage, last resort)
"""

from __future__ import annotations

import logging
from typing import List, Optional

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
    "Accept":          "application/json, text/html, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer":         "https://www.nextbank.com.tw/",
}

# Fallback URLs tried in order after the configured URL
_FALLBACK_URLS = [
    "https://www.nextbank.com.tw/exchange-rates",   # confirmed working
    "https://www.nextbank.com.tw/api/exchange-rates",
    "https://www.nextbank.com.tw/api/v2/exchange-rates",
    "https://www.nextbank.com.tw/api/v1/rates",
    "https://www.nextbank.com.tw/exchange-rate",
    "https://www.nextbank.com.tw/",
]


def fetch_usd_sell(config: dict) -> float:
    """
    Fetch NextBank USD sell rate (TWD per 1 USD).

    Args:
        config: The "nextbank" section from config.json, e.g.:
            {
                "url": "https://www.nextbank.com.tw/exchange-rates",
                "currency": "USD",
                "rate_type": "sell"
            }

    Returns:
        float — USD sell rate in TWD.

    Raises:
        ValueError: If the rate cannot be parsed from any URL.
        requests.RequestException: On network errors.
    """
    currency  = config.get("currency",  "USD").upper()
    rate_type = config.get("rate_type", "sell").lower()

    # Build URL list: configured URL first, then fallbacks (deduped)
    primary = config.get("url", "")
    urls: List[str] = [primary] if primary else []
    for u in _FALLBACK_URLS:
        if u not in urls:
            urls.append(u)

    last_error: Optional[str] = None

    for url in urls:
        logger.debug("Trying NextBank URL: %s", url)
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            logger.debug("  HTTP %s — skipping.", status)
            last_error = str(exc)
            continue
        except requests.RequestException as exc:
            logger.debug("  Request error: %s — skipping.", exc)
            last_error = str(exc)
            continue

        content_type = resp.headers.get("Content-Type", "")

        # ── Try JSON ──────────────────────────────────────────────────────
        if "json" in content_type or _looks_like_json(resp.text):
            try:
                rate = _parse_json(resp.json(), currency, rate_type)
                if rate is not None:
                    logger.info(
                        "NextBank %s %s = %.4f (JSON from %s)",
                        currency, rate_type, rate, url,
                    )
                    return rate
                logger.debug("  JSON parsed but %s %s not found.", currency, rate_type)
            except Exception as exc:
                logger.debug("  JSON parse error: %s", exc)

        # ── Try HTML ──────────────────────────────────────────────────────
        rate = _parse_html(resp.text, currency, rate_type)
        if rate is not None:
            logger.info(
                "NextBank %s %s = %.4f (HTML from %s)",
                currency, rate_type, rate, url,
            )
            return rate

        logger.debug("  Could not extract rate from %s", url)
        last_error = "Rate not found in response from {}".format(url)

    raise ValueError(
        "Cannot fetch NextBank {} {} rate from any URL.\n"
        "Last error: {}\n"
        "Please check config.json → nextbank.url and update it to the correct endpoint.\n"
        "Hint: Open https://www.nextbank.com.tw in a browser, find the exchange rate page,\n"
        "      then use browser DevTools (F12 → Network) to find the API call.".format(
            currency, rate_type, last_error
        )
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("{") or stripped.startswith("[")


def _parse_json(data, currency: str, rate_type: str) -> Optional[float]:
    """
    Try several common JSON shapes returned by Taiwan bank APIs.

    Shape A (array):   [{"currency": "USD", "sellRate": 32.5}, ...]
    Shape B (nested):  {"rates": {"USD": {"sell": 32.5}}}
    Shape C (flat):    {"USD": {"sell": 32.5}}
    Shape D (wrapper): {"data": [{"currencyCode": "USD", "cashSellRate": 32.5}]}
    """
    try:
        # Shape D — wrapper with "data" array
        if isinstance(data, dict) and "data" in data:
            items = data["data"]
            if isinstance(items, list):
                for item in items:
                    code = (item.get("currencyCode") or item.get("currency") or "").upper()
                    if code == currency:
                        rate = _extract_rate(item, rate_type)
                        if rate is not None:
                            return rate

        # Shape A — top-level array
        if isinstance(data, list):
            for item in data:
                code = (item.get("currencyCode") or item.get("currency") or "").upper()
                if code == currency:
                    rate = _extract_rate(item, rate_type)
                    if rate is not None:
                        return rate

        # Shape B — {"rates": {"USD": {...}}}
        if isinstance(data, dict) and "rates" in data:
            rates = data["rates"]
            if isinstance(rates, dict) and currency in rates:
                return _extract_rate(rates[currency], rate_type)

        # Shape C — {"USD": {...}}
        if isinstance(data, dict) and currency in data:
            return _extract_rate(data[currency], rate_type)

    except (KeyError, TypeError, ValueError) as exc:
        logger.debug("JSON parse attempt failed: %s", exc)

    return None


def _extract_rate(item: dict, rate_type: str) -> Optional[float]:
    """Extract sell/buy rate from a dict using common key names."""
    sell_keys = ["sellRate", "sell", "cashSellRate", "spotSellRate", "askRate", "ask", "賣出"]
    buy_keys  = ["buyRate",  "buy",  "cashBuyRate",  "spotBuyRate",  "bidRate", "bid", "買入"]
    keys = sell_keys if rate_type == "sell" else buy_keys
    for k in keys:
        if k in item:
            try:
                return float(str(item[k]).replace(",", ""))
            except (ValueError, TypeError):
                continue
    return None


def _parse_html(html: str, currency: str, rate_type: str) -> Optional[float]:
    """
    Fallback HTML scraper for exchange rate tables.

    Strategy:
      1. Find all <table> elements.
      2. Detect header row to locate the sell column index.
      3. Scan data rows for the currency code.
      4. Extract the value from the sell column (or any plausible TWD rate).
    """
    soup = BeautifulSoup(html, "lxml")

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        sell_col_idx: Optional[int] = None

        # Detect header row
        for row in rows:
            headers = [th.get_text(strip=True).lower() for th in row.find_all("th")]
            if not headers:
                # Some tables use <td> for headers
                headers = [td.get_text(strip=True).lower() for td in row.find_all("td")]
            if headers:
                for idx, h in enumerate(headers):
                    if rate_type in h or "sell" in h or "賣出" in h or "售出" in h:
                        sell_col_idx = idx
                        break
                if sell_col_idx is not None:
                    break

        # Scan data rows
        for row in rows:
            cells = row.find_all("td")
            if not cells:
                continue
            row_text = " ".join(c.get_text(strip=True) for c in cells)
            if currency.upper() not in row_text.upper():
                continue

            # Use detected sell column
            if sell_col_idx is not None and sell_col_idx < len(cells):
                text = cells[sell_col_idx].get_text(strip=True).replace(",", "")
                try:
                    val = float(text)
                    if 20.0 <= val <= 50.0:
                        return val
                except ValueError:
                    pass

            # Fallback: scan all cells for a plausible TWD rate (20–50)
            for cell in cells:
                text = cell.get_text(strip=True).replace(",", "")
                try:
                    val = float(text)
                    if 20.0 <= val <= 50.0:
                        return val
                except ValueError:
                    continue

    return None

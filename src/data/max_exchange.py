"""
MAX Exchange USDT/TWD sell rate fetcher.

Attempt 1 — MAX WebSocket API (real-time, bypasses Cloudflare HTTP block):
  wss://max-stream.maicoin.com/ws
  Subscribes to the public ticker channel for usdttwd.
  Returns the close price (C = last trade price) from the snapshot.
  No authentication required.

Attempt 2 — MAX REST API (fast, real-time):
  GET https://max.maicoin.com/api/v2/tickers/usdttwd
  Uses requests.Session with homepage warm-up to pass Cloudflare.

Attempt 3 — MAX HTML scraping (real-time, works when REST API is blocked):
  GET https://max.maicoin.com/markets/usdttwd
  MAX website is Next.js SSR; the initial HTML contains a <script id="__NEXT_DATA__">
  tag with full market data including the current close price.

Attempt 4 — CoinGecko public API (delayed ~1–5 min, last resort):
  GET https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=twd

WebSocket ticker field reference (from MAX docs):
  c  = channel ("ticker")
  e  = event ("snapshot" | "update")
  M  = market ("usdttwd")
  tk = ticker object:
    M = market
    O = open
    H = high
    L = low
    C = close (last trade price)  ← we use this
    v = volume
    V = volume in quote currency
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Optional

import requests
import websocket
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_TIMEOUT = 15

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Referer":         "https://max.maicoin.com/",
    "Origin":          "https://max.maicoin.com",
    "sec-ch-ua":       '"Chromium";v="124", "Google Chrome";v="124"',
    "sec-ch-ua-mobile": "?0",
    "sec-fetch-dest":  "empty",
    "sec-fetch-mode":  "cors",
    "sec-fetch-site":  "same-origin",
}

_COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=tether&vs_currencies=twd"
)


def fetch_usdt_twd_sell(config: dict) -> float:
    """
    Fetch MAX Exchange USDT/TWD sell (ask) price.

    Tries MAX API first (with session cookie warm-up), then falls back
    to CoinGecko if MAX is unavailable.

    Args:
        config: The "max_exchange" section from config.json, e.g.:
            {
                "url": "https://max.maicoin.com/api/v2/tickers/usdttwd",
                "side": "sell"
            }

    Returns:
        float — USDT sell price in TWD.

    Raises:
        ValueError: If the price cannot be parsed from any source.
        requests.RequestException: On network errors from all sources.
    """
    url  = config["url"]
    side = config.get("side", "sell").lower()

    # ── Attempt 1: MAX WebSocket (real-time, bypasses Cloudflare) ──────────
    ws_url = config.get("ws_url", "wss://max-stream.maicoin.com/ws")
    ws_timeout = int(config.get("ws_timeout", 10))
    try:
        price = _fetch_max_websocket(ws_url, ws_timeout)
        logger.info("MAX USDT/TWD = %.4f (WebSocket)", price)
        return price
    except Exception as exc:
        logger.warning("MAX WebSocket failed: %s — trying REST API.", exc)

    # ── Attempt 2: MAX REST API with session warm-up ───────────────────────
    try:
        price = _fetch_max_with_session(url, side)
        return price
    except Exception as exc:
        logger.warning("MAX REST API failed: %s — trying HTML scrape.", exc)

    # ── Attempt 3: MAX HTML scraping (Next.js __NEXT_DATA__) ───────────────
    try:
        market_url = config.get(
            "market_url", "https://max.maicoin.com/markets/usdttwd"
        )
        price = _fetch_max_html(market_url, side)
        if price is not None:
            logger.info("MAX USDT/TWD = %.4f (HTML scrape)", price)
            return price
        raise ValueError("HTML scrape returned None")
    except Exception as exc:
        logger.warning("MAX HTML scrape failed: %s — trying CoinGecko.", exc)

    # ── Attempt 4: CoinGecko fallback ─────────────────────────────────────
    try:
        price = _fetch_coingecko_twd()
        logger.info("Using CoinGecko USDT/TWD price as fallback: %.4f TWD", price)
        return price
    except Exception as exc:
        logger.error("CoinGecko fallback also failed: %s", exc)

    raise ValueError(
        "Cannot fetch USDT/TWD price from WebSocket, REST API, HTML scrape, "
        "or CoinGecko. Check network connectivity."
    )


def _fetch_max_websocket(ws_url: str, timeout: int = 10) -> float:
    """
    Fetch USDT/TWD last trade price via MAX WebSocket public ticker channel.

    Protocol:
      1. Connect to wss://max-stream.maicoin.com/ws
      2. Send subscription for {"channel": "ticker", "market": "usdttwd"}
      3. Receive "subscribed" ack, then "snapshot" with ticker data
      4. Extract tk.C (close = last trade price)
      5. Close connection

    The ticker channel does not provide bid/ask; tk.C is the last matched
    trade price, which is the best available real-time price indicator.

    Args:
        ws_url:  WebSocket endpoint (default: wss://max-stream.maicoin.com/ws)
        timeout: Seconds to wait for snapshot before raising (default: 10)

    Returns:
        float — USDT/TWD last trade price.

    Raises:
        ValueError: If no price received within timeout.
        Exception:  On WebSocket connection errors.
    """
    result: dict = {"price": None, "error": None}
    done = threading.Event()

    def on_open(ws: websocket.WebSocketApp) -> None:
        logger.debug("MAX WS: connected, subscribing to ticker usdttwd")
        sub_msg = {
            "action": "sub",
            "subscriptions": [{"channel": "ticker", "market": "usdttwd"}],
            "id": "monitor",
        }
        ws.send(json.dumps(sub_msg))

    def on_message(ws: websocket.WebSocketApp, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        channel = data.get("c")
        event   = data.get("e")

        # Wait for ticker snapshot (or update) for usdttwd
        if channel == "ticker" and event in ("snapshot", "update"):
            market = data.get("M", "")
            if market.lower() != "usdttwd":
                return
            tk = data.get("tk", {})
            raw = tk.get("C")   # close = last trade price
            if raw is not None:
                try:
                    result["price"] = float(raw)
                    logger.debug(
                        "MAX WS: ticker %s C=%s", market, raw
                    )
                except (ValueError, TypeError) as exc:
                    result["error"] = exc
                finally:
                    ws.close()
                    done.set()

    def on_error(ws: websocket.WebSocketApp, error: Exception) -> None:
        logger.debug("MAX WS error: %s", error)
        result["error"] = error
        done.set()

    def on_close(ws: websocket.WebSocketApp, code, msg) -> None:
        done.set()

    ws_app = websocket.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )

    thread = threading.Thread(
        target=ws_app.run_forever,
        kwargs={"ping_interval": 30, "ping_timeout": 10},
        daemon=True,
    )
    thread.start()

    received = done.wait(timeout=timeout)

    # Ensure connection is closed if we timed out
    if not received:
        ws_app.close()
        raise ValueError(
            f"MAX WebSocket: no ticker snapshot received within {timeout}s"
        )

    if result["error"] is not None:
        raise result["error"]

    if result["price"] is None:
        raise ValueError("MAX WebSocket: ticker snapshot had no close price")

    return result["price"]


def _fetch_max_with_session(url: str, side: str) -> float:
    """
    Use a requests.Session to warm up cookies by visiting the MAX homepage
    before calling the API endpoint.  This bypasses basic Cloudflare checks.
    """
    session = requests.Session()
    session.headers.update(_BROWSER_HEADERS)

    # Warm up: visit homepage to get Cloudflare clearance cookie
    logger.debug("MAX: warming up session via homepage...")
    try:
        session.get("https://max.maicoin.com/", timeout=_TIMEOUT)
        time.sleep(0.5)   # brief pause to appear more browser-like
    except Exception as exc:
        logger.debug("MAX homepage warm-up failed (non-fatal): %s", exc)

    # Now call the API
    logger.debug("MAX: calling API %s", url)
    resp = session.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()

    data   = resp.json()
    ticker = data.get("ticker")
    if not ticker:
        raise ValueError(
            "Unexpected MAX API response — missing 'ticker': {}".format(data)
        )

    raw = ticker.get(side)
    if raw is None:
        raise ValueError(
            "MAX ticker has no '{}' field. Keys: {}".format(side, list(ticker.keys()))
        )

    price = float(raw)
    logger.info("MAX USDT/TWD %s = %.4f (MAX API)", side, price)
    return price


def _fetch_max_html(market_url: str, side: str) -> Optional[float]:
    """
    Scrape the MAX market page for USDT/TWD price.

    MAX website is built with Next.js (SSR).  The server-rendered HTML
    contains a <script id="__NEXT_DATA__"> tag with full market data,
    including the current ticker sell/buy prices.

    Falls back to regex scanning the raw HTML if __NEXT_DATA__ is absent.

    Args:
        market_url: e.g. "https://max.maicoin.com/markets/usdttwd"
        side: "sell" or "buy"

    Returns:
        float price, or None if not found.
    """
    session = requests.Session()
    session.headers.update(_BROWSER_HEADERS)

    logger.debug("MAX HTML: fetching %s", market_url)
    resp = session.get(market_url, timeout=_TIMEOUT)
    resp.raise_for_status()

    html = resp.text

    # ── Strategy 1: Next.js __NEXT_DATA__ ─────────────────────────────────
    price = _parse_next_data(html, side)
    if price is not None:
        return price

    # ── Strategy 2: window.__INITIAL_STATE__ or similar ───────────────────
    price = _parse_initial_state(html, side)
    if price is not None:
        return price

    # ── Strategy 3: regex scan for sell/ask price in JSON fragments ────────
    price = _parse_html_regex(html, side)
    if price is not None:
        return price

    logger.debug("MAX HTML: no price found in page")
    return None


def _parse_next_data(html: str, side: str) -> Optional[float]:
    """Extract price from Next.js <script id='__NEXT_DATA__'> tag."""
    soup = BeautifulSoup(html, "lxml")
    tag  = soup.find("script", {"id": "__NEXT_DATA__"})
    if not tag:
        logger.debug("MAX HTML: no __NEXT_DATA__ tag found")
        return None

    try:
        data = json.loads(tag.string or "")
    except (json.JSONDecodeError, TypeError) as exc:
        logger.debug("MAX HTML: __NEXT_DATA__ JSON parse error: %s", exc)
        return None

    logger.debug("MAX HTML: __NEXT_DATA__ parsed (%d chars)", len(tag.string or ""))

    # Recursively search the JSON tree for a sell/ask price
    return _deep_find_price(data, side)


def _parse_initial_state(html: str, side: str) -> Optional[float]:
    """Extract price from window.__INITIAL_STATE__ or window.__PRELOADED_STATE__."""
    patterns = [
        r'window\.__INITIAL_STATE__\s*=\s*({.*?})(?:;|\s*</script>)',
        r'window\.__PRELOADED_STATE__\s*=\s*({.*?})(?:;|\s*</script>)',
        r'window\.__APP_STATE__\s*=\s*({.*?})(?:;|\s*</script>)',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                price = _deep_find_price(data, side)
                if price is not None:
                    return price
            except (json.JSONDecodeError, TypeError):
                continue
    return None


def _parse_html_regex(html: str, side: str) -> Optional[float]:
    """
    Last-resort regex scan: look for "sell":"31.59" or "ask":"31.59"
    patterns near 'usdt' or 'twd' context.
    """
    # Keys to look for based on side
    keys = ["sell", "ask"] if side == "sell" else ["buy", "bid"]

    for key in keys:
        # Pattern: "sell":"31.59" or "sell": 31.59
        pattern = rf'"{key}"\s*:\s*"?([0-9]{{2}}\.[0-9]{{2,6}})"?'
        matches = re.findall(pattern, html)
        for m in matches:
            try:
                val = float(m)
                if 25.0 <= val <= 45.0:   # plausible USDT/TWD range
                    logger.debug("MAX HTML regex: found %s=%s", key, val)
                    return val
            except ValueError:
                continue
    return None


def _deep_find_price(obj, side: str, depth: int = 0) -> Optional[float]:
    """
    Recursively search a JSON object for a USDT/TWD sell price.

    Looks for dicts that contain both a market identifier ('usdt', 'twd')
    and a price field matching the requested side.
    """
    if depth > 12:
        return None

    sell_keys = ["sell", "ask", "sellPrice", "askPrice", "last_price", "lastPrice"]
    buy_keys  = ["buy",  "bid", "buyPrice",  "bidPrice"]
    price_keys = sell_keys if side == "sell" else buy_keys

    if isinstance(obj, dict):
        # Check if this dict looks like a USDT/TWD ticker
        obj_str = json.dumps(obj).lower()
        is_usdt_market = (
            ("usdt" in obj_str or "tether" in obj_str)
            and ("twd" in obj_str)
        )

        if is_usdt_market:
            for key in price_keys:
                if key in obj:
                    try:
                        val = float(obj[key])
                        if 25.0 <= val <= 45.0:
                            logger.debug(
                                "MAX HTML deep_find: found %s=%s at depth %d",
                                key, val, depth,
                            )
                            return val
                    except (ValueError, TypeError):
                        pass

        # Recurse into values
        for v in obj.values():
            result = _deep_find_price(v, side, depth + 1)
            if result is not None:
                return result

    elif isinstance(obj, list):
        for item in obj:
            result = _deep_find_price(item, side, depth + 1)
            if result is not None:
                return result

    return None


def _fetch_coingecko_twd() -> float:
    """
    Fetch USDT/TWD price from CoinGecko public API.

    Returns:
        float — USDT price in TWD.
    """
    logger.debug("CoinGecko: fetching USDT/TWD from %s", _COINGECKO_URL)

    headers = {
        "User-Agent": "ExchangeRateMonitor/1.0",
        "Accept":     "application/json",
    }
    resp = requests.get(_COINGECKO_URL, headers=headers, timeout=_TIMEOUT)
    resp.raise_for_status()

    data = resp.json()
    # Response: {"tether": {"twd": 31.5}}
    twd_price = data.get("tether", {}).get("twd")
    if twd_price is None:
        raise ValueError("CoinGecko response missing tether.twd: {}".format(data))

    price = float(twd_price)
    logger.info("CoinGecko USDT/TWD = %.4f", price)
    return price

"""
Live LTP cache for Live Desk exits.

Primary: Aio-Trader KiteFeed WebSocket (``ltp`` mode) when ``aio-trader`` is installed.
Fallback: Kite REST quote/LTP when cache miss or stale (TTL).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

from domain.validation import safe_price
from settings import env

logger = logging.getLogger(__name__)

# --- config (override in .env) ---

def _ttl_sec() -> float:
    try:
        return max(1.0, float(env("LTP_CACHE_TTL_SEC", "15")))
    except ValueError:
        return 15.0


def _refresh_sec() -> float:
    try:
        return max(5.0, float(env("LTP_CACHE_REFRESH_SEC", "30")))
    except ValueError:
        return 30.0


def ws_enabled() -> bool:
    return env("LTP_CACHE_WS", "1").lower() not in {"0", "false", "no", "off"}


def rest_fallback_enabled() -> bool:
    return env("LTP_CACHE_REST_FALLBACK", "1").lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class LtpEntry:
    price: float
    updated_mono: float
    source: str  # "ws" | "rest"


def _position_key(exchange: str, tradingsymbol: str) -> str:
    return f"{exchange}:{tradingsymbol}"


def _ensure_windows_selector_loop() -> None:
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class LtpCache:
    """Thread-safe LTP store with optional async KiteFeed writer."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_key: dict[str, LtpEntry] = {}
        self._by_token: dict[int, LtpEntry] = {}
        self._token_to_key: dict[int, str] = {}
        self._subscribed: set[int] = set()
        self._feed: Any | None = None
        self._feed_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._started = False
        # Health counters
        self._total_updates = 0
        self._last_tick_mono: float | None = None
        self._started_mono: float | None = None
        self._reconnects = 0

    # ------------------------------------------------------------------ reads

    def get(
        self,
        exchange: str,
        tradingsymbol: str,
        *,
        instrument_token: int | None = None,
        allow_rest: bool = True,
    ) -> float | None:
        key = _position_key(exchange, tradingsymbol)
        px = self._get_fresh(key, instrument_token)
        if px is not None:
            return px
        if not allow_rest or not rest_fallback_enabled():
            return None
        fetched = self._rest_fetch([key])
        return fetched.get(key)

    def get_many(
        self,
        positions: list[dict[str, Any]],
        *,
        allow_rest: bool = True,
    ) -> dict[str, float]:
        """Resolve LTP for desk rows; batch REST only for stale/missing keys."""
        keys: list[str] = []
        key_tokens: dict[str, int | None] = {}
        for p in positions:
            exch = str(p.get("exchange") or "")
            sym = str(p.get("tradingsymbol") or "")
            if not exch or not sym:
                continue
            key = _position_key(exch, sym)
            keys.append(key)
            tok = p.get("instrument_token")
            key_tokens[key] = int(tok) if tok not in (None, "") else None

        out: dict[str, float] = {}
        missing: list[str] = []
        with self._lock:
            for key in keys:
                tok = key_tokens.get(key)
                entry = self._entry_for_key_locked(key, tok)
                if entry is not None and self._is_fresh(entry):
                    out[key] = entry.price
                else:
                    missing.append(key)

        if missing and allow_rest and rest_fallback_enabled():
            rest = self._rest_fetch(missing)
            out.update(rest)
        return out

    def status(self) -> dict[str, Any]:
        with self._lock:
            keys = len(self._by_key)
            tokens = len(self._by_token)
            subscribed = len(self._subscribed)
            sample = [
                {
                    "key": k,
                    "price": e.price,
                    "age_sec": round(time.monotonic() - e.updated_mono, 2),
                    "source": e.source,
                }
                for k, e in list(self._by_key.items())[:8]
            ]
        ws_ok = False
        try:
            import aio_trader  # noqa: F401

            aio_installed = True
        except ImportError:
            aio_installed = False
        return {
            "started": self._started,
            "ws_enabled": ws_enabled(),
            "aio_trader_installed": aio_installed,
            "feed_connected": bool(self._feed and getattr(self._feed, "connected", False)),
            "rest_fallback": rest_fallback_enabled(),
            "ttl_sec": _ttl_sec(),
            "refresh_sec": _refresh_sec(),
            "cached_keys": keys,
            "cached_tokens": tokens,
            "subscribed_tokens": subscribed,
            "sample": sample,
        }

    def health(self) -> dict[str, Any]:
        """Feed health for the trade-management safety gate and UI badge."""
        now = time.monotonic()
        with self._lock:
            cache_size = len(self._by_key)
            subscribed = len(self._subscribed)
            last_tick_mono = self._last_tick_mono
            total_updates = self._total_updates
            reconnects = self._reconnects
            started_mono = self._started_mono
        connected = bool(self._feed and getattr(self._feed, "connected", False))
        last_age = round(now - last_tick_mono, 2) if last_tick_mono is not None else None
        uptime = round(now - started_mono, 1) if started_mono is not None else None
        ttl = _ttl_sec()
        # Data is "flowing" if a tick arrived within a few TTL windows.
        data_flow = last_age is not None and last_age <= max(ttl * 3, 10.0)
        if not ws_enabled():
            status = "rest_only"
        elif connected and data_flow:
            status = "healthy"
        elif connected:
            status = "connected_no_data"
        else:
            status = "disconnected"
        return {
            "status": status,
            "ws_enabled": ws_enabled(),
            "feed_connected": connected,
            "rest_fallback": rest_fallback_enabled(),
            "data_flow_healthy": bool(data_flow),
            "last_tick_age_sec": last_age,
            "total_updates": total_updates,
            "reconnects": reconnects,
            "uptime_sec": uptime,
            "cache_size": cache_size,
            "subscribed_tokens": subscribed,
            "ttl_sec": ttl,
        }

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Current cached price per key with age/source, for WS push to the UI."""
        now = time.monotonic()
        with self._lock:
            return {
                key: {
                    "price": e.price,
                    "age_sec": round(now - e.updated_mono, 2),
                    "source": e.source,
                    "fresh": (now - e.updated_mono) <= _ttl_sec(),
                }
                for key, e in self._by_key.items()
            }

    def rest_prices(self, positions: list[dict[str, Any]]) -> dict[str, float]:
        """Force an authoritative REST re-fetch for the given positions (bypass WS cache)."""
        keys = []
        for p in positions:
            exch = str(p.get("exchange") or "")
            sym = str(p.get("tradingsymbol") or "")
            if exch and sym:
                keys.append(_position_key(exch, sym))
        return self._rest_fetch(keys) if keys else {}

    # ------------------------------------------------------------------ writes

    def register(self, exchange: str, tradingsymbol: str, instrument_token: int) -> None:
        key = _position_key(exchange, tradingsymbol)
        with self._lock:
            self._token_to_key[int(instrument_token)] = key

    def ingest_ws_ticks(self, ticks: list[dict[str, Any]] | None) -> None:
        now = time.monotonic()
        with self._lock:
            for tick in ticks or []:
                if not tick:
                    continue
                price = safe_price(tick.get("last_price"))
                if price is None:
                    continue  # drop NaN/inf/<=0/blank ticks — never poison the cache
                token = int(tick["instrument_token"])
                entry = LtpEntry(price, now, "ws")
                self._by_token[token] = entry
                self._total_updates += 1
                self._last_tick_mono = now
                key = self._token_to_key.get(token)
                if key:
                    self._by_key[key] = entry

    def _store_rest(self, prices: dict[str, float]) -> None:
        now = time.monotonic()
        with self._lock:
            for key, px in prices.items():
                entry = LtpEntry(float(px), now, "rest")
                self._by_key[key] = entry

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
        if not ws_enabled():
            logger.info("LTP cache: WebSocket disabled (LTP_CACHE_WS=0)")
            return
        try:
            import aio_trader  # noqa: F401
        except ImportError:
            logger.warning(
                "LTP cache: aio-trader not installed — REST fallback only. "
                "pip install git+https://github.com/BennyThadikaran/Aio-Trader.git"
            )
            return
        from kite_auth import session_status

        if not session_status().get("authenticated"):
            logger.info("LTP cache: no Kite session — feed deferred until login")
            return
        _ensure_windows_selector_loop()
        self._started_mono = time.monotonic()
        self._feed_task = asyncio.create_task(self._feed_runner(), name="ltp-kite-feed")
        logger.info("LTP cache: KiteFeed background task started")

    async def stop(self) -> None:
        self._stop.set()
        if self._feed_task:
            self._feed_task.cancel()
            try:
                await self._feed_task
            except asyncio.CancelledError:
                pass
            self._feed_task = None
        if self._feed:
            try:
                await self._feed.close()
            except Exception:
                pass
            self._feed = None
        self._started = False

    async def restart_if_authenticated(self) -> None:
        """Call after Kite login so the feed picks up access_token."""
        await self.stop()
        self._started = False
        await self.start()

    # ------------------------------------------------------------------ internals

    def _get_fresh(self, key: str, token: int | None) -> float | None:
        with self._lock:
            entry = self._entry_for_key_locked(key, token)
            if entry is not None and self._is_fresh(entry):
                return entry.price
        return None

    def _entry_for_key_locked(self, key: str, token: int | None) -> LtpEntry | None:
        entry = self._by_key.get(key)
        if entry is not None:
            return entry
        if token is not None:
            return self._by_token.get(int(token))
        return None

    def _is_fresh(self, entry: LtpEntry) -> bool:
        return (time.monotonic() - entry.updated_mono) <= _ttl_sec()

    def _desired_tokens(self) -> set[int]:
        tokens: set[int] = set()
        try:
            from watchlist_store import list_items

            for item in list_items("active"):
                try:
                    from execution.watchlist_runner import chart_instrument_meta

                    meta = chart_instrument_meta(item)
                    tok = int(meta["instrument_token"])
                    exch = str(meta.get("exchange") or item.get("exchange") or "")
                    sym = str(meta.get("tradingsymbol") or item.get("tradingsymbol") or "")
                    if exch and sym:
                        self.register(exch, sym, tok)
                    tokens.add(tok)
                except Exception:
                    continue
        except Exception:
            pass
        try:
            from broker.kite_broker import KiteBroker
            from execution.arming import get_arm_state

            if get_arm_state().get("mode") == "live":
                for pos in KiteBroker().positions():
                    qty = int(pos.get("quantity") or 0)
                    if qty == 0:
                        continue
                    exch = str(pos.get("exchange") or "")
                    sym = str(pos.get("tradingsymbol") or "")
                    tok = pos.get("instrument_token")
                    if exch and sym and tok not in (None, ""):
                        self.register(exch, sym, int(tok))
                        tokens.add(int(tok))
        except Exception:
            pass
        return tokens

    def _rest_fetch(self, keys: list[str]) -> dict[str, float]:
        if not keys:
            return {}
        out: dict[str, float] = {}
        try:
            from kite_client import fetch_quote_batch, session_status

            if not session_status().get("authenticated"):
                return out
            quotes = fetch_quote_batch(keys)
            for key, q in quotes.items():
                px = safe_price(q.get("last_price"))
                if px is not None:
                    out[key] = px
        except Exception as exc:
            logger.debug("LTP REST quote batch failed: %s", exc)
        if len(out) < len(keys):
            try:
                from kite_client import fetch_ltp_batch

                missing = [k for k in keys if k not in out]
                for i in range(0, len(missing), 400):
                    chunk = missing[i : i + 400]
                    data = fetch_ltp_batch(chunk)
                    for key, row in data.items():
                        px = safe_price(row.get("last_price"))
                        if px is not None:
                            out[key] = px
            except Exception as exc:
                logger.debug("LTP REST ltp fallback failed: %s", exc)
        if out:
            self._store_rest(out)
        return out

    async def _sync_subscriptions(self, feed: Any, desired: set[int]) -> None:
        with self._lock:
            current = set(self._subscribed)
        to_add = sorted(desired - current)
        to_remove = sorted(current - desired)
        if to_remove:
            await feed.unsubscribe(to_remove)
        if to_add:
            await feed.subscribe(to_add)
            await feed.set_mode(feed.MODE_LTP, to_add)
        with self._lock:
            self._subscribed = set(desired)

    async def _subscription_loop(self, feed: Any) -> None:
        while not self._stop.is_set():
            try:
                desired = self._desired_tokens()
                if desired:
                    await self._sync_subscriptions(feed, desired)
            except Exception as exc:
                logger.warning("LTP subscription refresh failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_refresh_sec())
                break
            except asyncio.TimeoutError:
                continue

    def _on_tick(self, _feed: Any, ticks: list[dict[str, Any]]) -> None:
        self.ingest_ws_ticks(ticks)

    async def _feed_runner(self) -> None:
        from aio_trader.kite import KiteFeed
        from kite_auth import load_session
        from settings import kite_credentials

        while not self._stop.is_set():
            sess = load_session()
            if not sess or not sess.get("access_token"):
                await asyncio.sleep(5.0)
                continue
            creds = kite_credentials()
            api_key = creds.get("api_key") or ""
            if not api_key:
                logger.warning("LTP cache: KITE_API_KEY missing — feed idle")
                await asyncio.sleep(30.0)
                continue

            feed = KiteFeed(
                api_key=api_key,
                access_token=str(sess["access_token"]),
            )
            feed.on_tick = self._on_tick
            self._feed = feed
            sub_task = asyncio.create_task(self._subscription_loop(feed), name="ltp-sub-refresh")
            try:
                await feed.connect()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("LTP KiteFeed disconnected: %s", exc)
            finally:
                sub_task.cancel()
                try:
                    await sub_task
                except asyncio.CancelledError:
                    pass
                try:
                    await feed.close()
                except Exception:
                    pass
                self._feed = None
                with self._lock:
                    self._subscribed.clear()

            if self._stop.is_set():
                break
            with self._lock:
                self._reconnects += 1
            await asyncio.sleep(3.0)


_cache = LtpCache()


def get_ltp_cache() -> LtpCache:
    return _cache


async def start_ltp_feed() -> None:
    await _cache.start()


async def stop_ltp_feed() -> None:
    await _cache.stop()


def fetch_ltps_for_positions(positions: list[dict[str, Any]]) -> dict[str, float]:
    """Primary entry for desk / exit runner LTP resolution."""
    return _cache.get_many(positions)


def market_health() -> dict[str, Any]:
    """Feed health snapshot for the UI badge and safety gate."""
    return _cache.health()


def is_trade_management_safe(max_age_sec: float | None = None) -> tuple[bool, str]:
    """
    Whether it is safe to act on price-based exits (SL/TSL/target).

    Safe when a confirmed price source exists: a fresh WS tick, or REST fallback
    with an authenticated Kite session. Otherwise the exit runner should defer
    the tick rather than act on stale/missing data.
    """
    h = _cache.health()
    ttl = _ttl_sec()
    max_age = max_age_sec if max_age_sec is not None else max(ttl * 2, 10.0)
    age = h.get("last_tick_age_sec")
    if h.get("feed_connected") and age is not None and age <= max_age:
        return True, f"Live feed healthy (last tick {age:.1f}s ago)"
    if rest_fallback_enabled():
        try:
            from kite_client import session_status

            if session_status().get("authenticated"):
                return True, "WS stale — REST reconfirm available"
        except Exception:
            pass
        return False, "WS stale and Kite session not authenticated"
    return False, "WS feed down and REST fallback disabled"

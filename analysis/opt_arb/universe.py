"""Instrument selection for the arbitrage scanner.

Reads the Kite instrument dump directly through ``instruments.load_instruments``
rather than going via ``config.INDEX_OPTIONS`` / ``options.chain``. Two reasons:

1. ``INDEX_OPTIONS`` does not carry the fields this desk needs — contract size
   per lot, the quote unit, and the big/mini partner.
2. ``config.ANALYTICS_HISTORY_SAMPLE_UNDERLYINGS`` is filtered against
   ``INDEX_OPTIONS`` at call time, so adding GOLD/SILVER there to make them
   scannable would silently switch on 30-second Gamma-Density and OI-VAR
   background sampling for them as a side effect. Keeping this desk's universe
   self-contained avoids paying that quota for a desk that does not want it.

**Contract sizes are the load-bearing constants here.** A wrong multiplier does
not produce an obviously broken screen, it produces a plausible one with the
wrong money on it. They are stated per MCX contract specification and asserted
in ``tests/test_opt_arb_universe.py``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import pandas as pd

from instruments import load_instruments

# NFO/BFO names that are cash-settled indices. Everything else on NFO is a
# stock option, which is physically settled — a distinction the cost model and
# the box detector both care about.
INDEX_NAMES = frozenset(
    {
        "NIFTY",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "NIFTYNXT50",
        "SENSEX",
        "SENSEX50",
        "BANKEX",
        "FOCIT",
    }
)


@dataclass(frozen=True)
class MiniPair:
    """A big contract and its mini, quoted in the same unit.

    ``units_per_lot`` is what turns a per-unit premium into rupees. Gold is
    quoted per 10 g, so GOLD (1 kg) is 100 of those units and GOLDM (100 g) is
    10 — the premium columns are directly comparable, only the money differs.
    """

    key: str
    big: str
    mini: str
    exchange: str
    big_units_per_lot: float
    mini_units_per_lot: float
    unit: str
    label: str

    @property
    def ratio(self) -> float:
        """Mini lots needed to offset one big lot."""
        return self.big_units_per_lot / self.mini_units_per_lot


MINI_PAIRS: tuple[MiniPair, ...] = (
    MiniPair(
        key="CRUDEOIL_CRUDEOILM",
        big="CRUDEOIL",
        mini="CRUDEOILM",
        exchange="MCX",
        big_units_per_lot=100.0,
        mini_units_per_lot=10.0,
        unit="bbl",
        label="Crude Oil vs Crude Oil Mini",
    ),
    MiniPair(
        key="NATURALGAS_NATGASMINI",
        big="NATURALGAS",
        mini="NATGASMINI",
        exchange="MCX",
        big_units_per_lot=1250.0,
        mini_units_per_lot=250.0,
        unit="mmBtu",
        label="Natural Gas vs Natural Gas Mini",
    ),
    MiniPair(
        key="GOLD_GOLDM",
        big="GOLD",
        mini="GOLDM",
        exchange="MCX",
        big_units_per_lot=100.0,
        mini_units_per_lot=10.0,
        unit="10 g",
        label="Gold vs Gold Mini",
    ),
    MiniPair(
        key="SILVER_SILVERM",
        big="SILVER",
        mini="SILVERM",
        exchange="MCX",
        big_units_per_lot=30.0,
        mini_units_per_lot=5.0,
        unit="kg",
        label="Silver vs Silver Mini",
    ),
)

_PAIRS_BY_KEY = {p.key: p for p in MINI_PAIRS}

# Units behind one MCX option lot, in the unit the contract is *quoted* in.
# Kite reports ``lot_size = 1`` for every MCX option, so premium x lot_size
# would value every commodity leg at one rupee per point. These are the
# multipliers that turn a quoted premium into money.
MCX_UNITS_PER_LOT: dict[str, float] = {
    "GOLD": 100.0,  # 1 kg, quoted per 10 g
    "GOLDM": 10.0,  # 100 g, quoted per 10 g
    "SILVER": 30.0,  # kg
    "SILVERM": 5.0,  # kg
    "CRUDEOIL": 100.0,  # bbl
    "CRUDEOILM": 10.0,  # bbl
    "NATURALGAS": 1250.0,  # mmBtu
    "NATGASMINI": 250.0,  # mmBtu
    "COPPER": 2500.0,  # kg
    "ZINC": 5000.0,  # kg
    "MCXBULLDEX": 50.0,  # index points
}


def pair_by_key(key: str) -> MiniPair | None:
    return _PAIRS_BY_KEY.get(str(key or "").upper())


def units_per_lot(exchange: str, name: str, lot_size: int | float | None = None) -> float:
    """Underlying quantity behind one lot — the premium-to-rupees multiplier.

    NFO/BFO carry a usable ``lot_size`` in the instrument dump. MCX does not
    (every option row says 1), so commodities resolve through
    ``MCX_UNITS_PER_LOT``; an unlisted MCX name returns 0 so a caller can skip
    it rather than silently price it at 1x.
    """
    ex = str(exchange or "").upper()
    if ex == "MCX":
        return float(MCX_UNITS_PER_LOT.get(str(name or "").upper(), 0.0))
    try:
        size = float(lot_size or 0)
    except (TypeError, ValueError):
        size = 0.0
    return size


def lots_available(depth_qty: int | float | None, lot_size: int | float | None) -> int:
    """Top-of-book quantity expressed in lots.

    The units differ by segment and getting this wrong silently inflates the
    size column by the lot size: Kite reports NFO/BFO depth in *underlying
    units* (65 on a NIFTY option is one lot), while MCX rows carry
    ``lot_size = 1`` and report depth in lots already. Dividing by the dump's
    own ``lot_size`` is correct in both cases.
    """
    try:
        qty = float(depth_qty or 0)
        size = float(lot_size or 0)
    except (TypeError, ValueError):
        return 0
    if size <= 0:
        return 0
    return max(int(qty // size), 0)


def cost_segment(exchange: str, name: str) -> str:
    """Which :mod:`analysis.opt_arb.costs` rate card applies."""
    ex = str(exchange or "").upper()
    if ex == "MCX":
        return "MCX"
    if ex == "BFO":
        return "BFO"
    if ex == "NFO" and str(name or "").upper() not in INDEX_NAMES:
        return "NFO_STOCK"
    return "NFO"


def is_physically_settled(exchange: str, name: str) -> bool:
    return cost_segment(exchange, name) == "NFO_STOCK"


def _to_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        parsed = pd.to_datetime(value, errors="coerce")
    except (TypeError, ValueError):
        return None
    if parsed is None or pd.isna(parsed):
        return None
    return parsed.date()


# (name, exchange, instrument kind) -> (instruments-cache mtime, frame). The
# dump is ~113k rows and changes once a day, but a single sweep asks for the
# same underlying five or six times (expiries, then a strike map per detector).
# Rescanning per call cost ~1.4s each and made a full sweep take ~50s; the same
# mtime guard options/chain.py uses takes it to milliseconds.
_FRAME_CACHE: dict[tuple[str, str, str], tuple[float, pd.DataFrame]] = {}
_CONTRACT_CACHE: dict[tuple[str, str, str], tuple[float, list[dict[str, Any]]]] = {}


def _dump_mtime() -> float:
    try:
        from instruments import CACHE_FILE

        return CACHE_FILE.stat().st_mtime if CACHE_FILE.exists() else 0.0
    except (OSError, ImportError):
        return 0.0


def clear_caches() -> None:
    """Drop the memoised frames — for tests that swap the instrument dump."""
    _FRAME_CACHE.clear()
    _CONTRACT_CACHE.clear()


def _frame(name: str, exchange: str, kind: str) -> pd.DataFrame:
    """Rows for one underlying, with expiries converted once, not per row.

    ``_to_date`` on a per-element basis was the other half of the cost: a
    vectorised ``pd.to_datetime`` over the column is orders of magnitude
    cheaper than calling it 3,000 times.
    """
    key = (str(name).upper(), str(exchange).upper(), kind)
    mtime = _dump_mtime()
    cached = _FRAME_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    df = load_instruments()
    if df.empty:
        _FRAME_CACHE[key] = (mtime, df)
        return df

    types = ["CE", "PE"] if kind == "option" else ["FUT"]
    mask = (
        (df["exchange"].astype(str).str.upper() == key[1])
        & (df["name"].astype(str).str.upper() == key[0])
        & (df["instrument_type"].astype(str).str.upper().isin(types))
    )
    frame = df[mask].copy()
    if not frame.empty:
        frame["_exp"] = pd.to_datetime(frame["expiry"], errors="coerce").dt.date
        frame = frame.dropna(subset=["_exp"])
    _FRAME_CACHE[key] = (mtime, frame)
    return frame


def _options_frame(name: str, exchange: str) -> pd.DataFrame:
    return _frame(name, exchange, "option")


def _expiry_list(name: str, exchange: str, kind: str) -> list[date]:
    frame = _frame(name, exchange, kind)
    if frame.empty:
        return []
    return sorted(set(frame["_exp"]))


def option_expiries(name: str, exchange: str, *, include_past: bool = False) -> list[str]:
    """Sorted ISO option expiries for one underlying."""
    days = _expiry_list(name, exchange, "option")
    if not include_past:
        today = date.today()
        days = [d for d in days if d >= today]
    return [d.isoformat() for d in days]


def future_expiries(name: str, exchange: str) -> list[str]:
    return [d.isoformat() for d in _expiry_list(name, exchange, "future")]


def referenced_future(name: str, exchange: str, option_expiry: str) -> str | None:
    """The futures contract an option expiry settles against.

    MCX options expire a few days *before* the future they devolve into, so the
    reference is the first future expiring on or after the option expiry. This
    is the field that decides whether a big/mini pair is comparable at all: a
    GOLD option pointing at the October future and a GOLDM option pointing at
    September are separated by a month of carry, not by mispricing.
    """
    target = _to_date(option_expiry)
    if target is None:
        return None
    for parsed in _expiry_list(name, exchange, "future"):
        if parsed >= target:
            return parsed.isoformat()
    return None


def contracts(name: str, exchange: str, expiry: str) -> list[dict[str, Any]]:
    """CE/PE contracts for one underlying and expiry, sorted by strike."""
    target = _to_date(expiry)
    if target is None:
        return []
    key = (str(name).upper(), str(exchange).upper(), target.isoformat())
    mtime = _dump_mtime()
    cached = _CONTRACT_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return [dict(row) for row in cached[1]]

    frame = _options_frame(name, exchange)
    rows: list[dict[str, Any]] = []
    if not frame.empty:
        for row in frame[frame["_exp"] == target].itertuples():
            rows.append(
                {
                    "tradingsymbol": str(row.tradingsymbol).upper(),
                    "instrument_token": int(row.instrument_token),
                    "exchange": str(row.exchange).upper(),
                    "name": str(row.name).upper(),
                    "expiry": target.isoformat(),
                    "strike": float(row.strike),
                    "option_type": str(row.instrument_type).upper(),
                    "lot_size": int(row.lot_size or 1),
                }
            )
        rows.sort(key=lambda r: (r["strike"], r["option_type"]))

    _CONTRACT_CACHE[key] = (mtime, rows)
    # Callers enrich these dicts, so hand out copies rather than the cached ones.
    return [dict(row) for row in rows]


def strike_map(name: str, exchange: str, expiry: str) -> dict[float, dict[str, dict[str, Any]]]:
    """``{strike: {"CE": contract, "PE": contract}}`` for one expiry."""
    out: dict[float, dict[str, dict[str, Any]]] = {}
    for row in contracts(name, exchange, expiry):
        out.setdefault(row["strike"], {})[row["option_type"]] = row
    return out


def expiry_status(pair: MiniPair, big_expiry: str, mini_expiry: str) -> dict[str, Any]:
    """Classify **one expiry pair**, not the contract pair as a whole.

    This distinction is load-bearing and cost real opportunities before it was
    made: GOLD and GOLDM do not line up in the *front* month (31 Aug vs 28 Aug,
    pointing at the October and September futures), but at the 25 Sep expiry
    they share both the date and the October future — a genuine Tier A trade
    that a front-month-only classification hides completely.

    SILVER/SILVERM is the opposite case and stays dirty at every shared expiry:
    the two futures cycles never coincide.
    """
    big_fut = referenced_future(pair.big, pair.exchange, big_expiry) if big_expiry else None
    mini_fut = referenced_future(pair.mini, pair.exchange, mini_expiry) if mini_expiry else None

    expiry_match = bool(big_expiry and mini_expiry and big_expiry == mini_expiry)
    future_match = bool(big_fut and mini_fut and big_fut == mini_fut)
    clean = expiry_match and future_match

    if clean:
        reason = "same option expiry and same underlying futures month"
    elif not expiry_match and not future_match:
        reason = (
            f"option expiries differ ({big_expiry} vs {mini_expiry}) and they reference "
            f"different futures months ({big_fut} vs {mini_fut}) — carry spread, not arbitrage"
        )
    elif not future_match:
        reason = (
            f"same option expiry but different underlying futures "
            f"({big_fut} vs {mini_fut}) — one month of carry sits in this spread"
        )
    else:
        reason = f"option expiries differ ({big_expiry} vs {mini_expiry})"

    return {
        "clean": clean,
        "reason": reason,
        "expiry": {"big": big_expiry, "mini": mini_expiry, "matched": expiry_match},
        "referenced_future": {"big": big_fut, "mini": mini_fut, "matched": future_match},
    }


def pair_status(pair: MiniPair) -> dict[str, Any]:
    """Summary of a big/mini pair across every expiry it lists.

    ``clean_expiries`` is the field that matters: the shared expiries where both
    sides also reference the same futures month. A pair can be dirty in the
    front month and clean further out — GOLD/GOLDM is exactly that today — so
    ``clean`` here means "there is at least one tradable expiry", and callers
    that price a specific expiry must classify it with :func:`expiry_status`
    rather than reusing this flag.

    ``front`` keeps the front-month view for display, because that is the
    contract an operator looks at first and its being dirty is worth seeing.
    """
    big_opts = option_expiries(pair.big, pair.exchange)
    mini_opts = option_expiries(pair.mini, pair.exchange)
    shared = [e for e in big_opts if e in set(mini_opts)]

    front_big = big_opts[0] if big_opts else None
    front_mini = mini_opts[0] if mini_opts else None
    front = expiry_status(pair, front_big or "", front_mini or "")

    per_expiry = {e: expiry_status(pair, e, e) for e in shared}
    clean_expiries = [e for e, st in per_expiry.items() if st["clean"]]

    if clean_expiries:
        if front["clean"]:
            reason = "same option expiry and same underlying futures month"
        else:
            reason = (
                f"front month is a carry spread ({front['reason']}), but "
                f"{clean_expiries[0]} lines up on both expiry and futures month"
            )
    else:
        reason = front["reason"]

    return {
        **asdict(pair),
        "ratio": pair.ratio,
        "clean": bool(clean_expiries),
        "reason": reason,
        "front_clean": front["clean"],
        "front_reason": front["reason"],
        "front_expiry": front["expiry"],
        "referenced_future": front["referenced_future"],
        "shared_expiries": shared,
        "clean_expiries": clean_expiries,
        "expiry_status": per_expiry,
    }


def pair_registry() -> list[dict[str, Any]]:
    """Every big/mini pair with its live clean/carry classification."""
    return [pair_status(p) for p in MINI_PAIRS]

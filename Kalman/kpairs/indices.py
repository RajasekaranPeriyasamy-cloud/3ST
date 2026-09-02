"""NSE index universe for the timeframe study, split by what you can actually trade.

Tier 1 -- index futures exist. A pair here is directly implementable: two
futures legs, no borrow problem, deep books, and the two legs settle on the
same expiry so the basis largely cancels.

Tier 2 -- sectoral and thematic indices with **no listed derivative**. A
NIFTY IT / NIFTY PHARMA spread is a perfectly good research object and a
perfectly bad order ticket. It can be approximated with sector ETFs (thin, wide
spreads, tracking error) or a replicating basket (10-15 legs per side, which
destroys the cost budget at intraday frequency). Everything Tier 2 produces
below is reported separately and should be read as an upper bound on an
implementable version, not as a strategy.

BSE SENSEX / BANKEX are included in Tier 1 because they have futures and are
the natural cross-exchange counterparts to NIFTY / BANKNIFTY -- historically the
tightest index pair on the tape.
"""

from __future__ import annotations

# tradingsymbol -> (exchange, short label, has_futures)
TIER1: dict[str, tuple[str, str, bool]] = {
    "NIFTY 50":          ("NSE", "NIFTY", True),
    "NIFTY BANK":        ("NSE", "BANKNIFTY", True),
    "NIFTY FIN SERVICE": ("NSE", "FINNIFTY", True),
    "NIFTY MID SELECT":  ("NSE", "MIDCPNIFTY", True),
    "SENSEX":            ("BSE", "SENSEX", True),
    "BANKEX":            ("BSE", "BANKEX", True),
}

TIER2: dict[str, tuple[str, str, bool]] = {
    "NIFTY IT":          ("NSE", "IT", False),
    "NIFTY PHARMA":      ("NSE", "PHARMA", False),
    "NIFTY AUTO":        ("NSE", "AUTO", False),
    "NIFTY FMCG":        ("NSE", "FMCG", False),
    "NIFTY METAL":       ("NSE", "METAL", False),
    "NIFTY ENERGY":      ("NSE", "ENERGY", False),
    "NIFTY REALTY":      ("NSE", "REALTY", False),
    "NIFTY PSU BANK":    ("NSE", "PSUBANK", False),
    "NIFTY PVT BANK":    ("NSE", "PVTBANK", False),
    "NIFTY MEDIA":       ("NSE", "MEDIA", False),
    "NIFTY INFRA":       ("NSE", "INFRA", False),
    "NIFTY CONSUMPTION": ("NSE", "CONSUMPTION", False),
    "NIFTY PSE":         ("NSE", "PSE", False),
    "NIFTY CPSE":        ("NSE", "CPSE", False),
    "NIFTY COMMODITIES": ("NSE", "COMMODITIES", False),
    "NIFTY SERV SECTOR": ("NSE", "SERVICES", False),
    "NIFTY HEALTHCARE":  ("NSE", "HEALTHCARE", False),
    "NIFTY OIL AND GAS": ("NSE", "OILGAS", False),
    "NIFTY CONSR DURBL": ("NSE", "CONSRDURBL", False),
    "NIFTY NEXT 50":     ("NSE", "NEXT50", False),
    "NIFTY MIDCAP 100":  ("NSE", "MIDCAP100", False),
    "NIFTY SMLCAP 100":  ("NSE", "SMLCAP100", False),
}

ALL = {**TIER1, **TIER2}


def get_universe(tier: str) -> dict[str, tuple[str, str, bool]]:
    t = tier.strip().lower()
    if t in {"1", "tier1", "tradable", "futures"}:
        return dict(TIER1)
    if t in {"2", "tier2", "sectoral"}:
        return dict(TIER2)
    if t in {"all", "both"}:
        return dict(ALL)
    raise ValueError(f"unknown tier {tier!r}; use tier1 | tier2 | all")


def resolve_tokens(members: dict[str, tuple[str, str, bool]]) -> dict[str, dict]:
    """Resolve index tradingsymbols to Kite instrument tokens.

    Returns ``{label: {"token": int, "symbol": str, "exchange": str,
    "futures": bool}}`` keyed by the short label, because ``NIFTY FIN SERVICE``
    is a poor column name and ``FINNIFTY`` is what everyone calls it anyway.
    """
    from instruments import load_instruments

    df = load_instruments()
    idx = df[df["segment"] == "INDICES"]

    out: dict[str, dict] = {}
    missing: list[str] = []
    for tsym, (exch, label, fut) in members.items():
        row = idx[(idx["exchange"] == exch) & (idx["tradingsymbol"] == tsym)]
        if row.empty:
            missing.append(f"{exch}:{tsym}")
            continue
        out[label] = {
            "token": int(row.iloc[0]["instrument_token"]),
            "symbol": tsym,
            "exchange": exch,
            "futures": fut,
        }
    if missing:
        print(f"[indices] not found in dump, dropped: {', '.join(missing)}")
    return out


def has_futures(label: str) -> bool:
    for _, (_, lbl, fut) in ALL.items():
        if lbl == label:
            return fut
    return False


def pair_is_tradable(a: str, b: str) -> bool:
    """Both legs need a listed future for the spread to be an order, not an idea."""
    return has_futures(a) and has_futures(b)

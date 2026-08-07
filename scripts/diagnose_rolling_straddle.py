#!/usr/bin/env python3
"""Rolling Straddle underlying diagnostics — run from repo root.

Usage:
  .venv\\Scripts\\python.exe scripts/diagnose_rolling_straddle.py
  .venv\\Scripts\\python.exe scripts/diagnose_rolling_straddle.py CRUDEOIL
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

UNDERLYINGS = ("NIFTY", "BANKNIFTY", "SENSEX", "CRUDEOIL", "CRUDEOILM", "NATURALGAS")
API = "http://127.0.0.1:8001"


def _get(path: str) -> dict:
    try:
        with urllib.request.urlopen(f"{API}{path}", timeout=12) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        return {"error": str(exc)}


def _python_spot(u: str) -> tuple[float | None, str | None]:
    try:
        from options.chain import get_index_spot_detail, list_expiries

        ex = list_expiries(u)
        spot, err = get_index_spot_detail(u)
        return spot, err or (None if ex else "no expiries in instruments cache")
    except Exception as exc:
        return None, str(exc)


def diagnose_one(u: str) -> None:
    print(f"\n=== {u} ===")
    exp = _get(f"/options/expiries?underlying={u}")
    if exp.get("error"):
        print(f"  expiries API: FAIL — {exp['error']}")
    else:
        items = exp.get("expiries") or []
        print(f"  expiries API: {len(items)} dates, nearest={items[0] if items else 'none'}")

    spot, err = _python_spot(u)
    if spot is not None:
        print(f"  spot LTP: {spot}")
    else:
        print(f"  spot LTP: FAIL — {err or 'unknown'}")


def main() -> int:
    targets = [sys.argv[1].upper()] if len(sys.argv) > 1 else list(UNDERLYINGS)

    health = _get("/health")
    if health.get("error"):
        print(f"API unreachable at {API}: {health['error']}")
        return 1

    print("API health:")
    print(f"  kite_authenticated={health.get('kite_authenticated')}")
    print(f"  instruments_cache={health.get('instruments_cache')} age_days={health.get('instruments_cache_age_days')}")

    cfg = _get("/live/rolling-straddle/config")
    st = _get("/live/rolling-straddle/status?light=1")
    if not cfg.get("error"):
        print(f"\nSaved config: underlying={cfg.get('underlying')} expiry={cfg.get('expiry')}")
    if not st.get("error"):
        state = st.get("state") or {}
        print(
            f"Live state: spot={state.get('last_spot')} atm={state.get('current_atm')} "
            f"marker={state.get('state_underlying')} runner={state.get('runner')}"
        )

    for u in targets:
        if u not in UNDERLYINGS:
            print(f"Unknown underlying {u}; use {UNDERLYINGS}")
            continue
        diagnose_one(u)

    print("\nTips:")
    print("  1. Change underlying → click Save (clears stale spot/ATM)")
    print("  2. Session times are manual — NSE/BSE ~09:15-15:30, MCX ~09:15-23:30")
    print("  3. Spot needs market hours + Kite login; check Activity log for 'error' rows")
    print("  4. Refresh instruments: GET /instruments?refresh=true after Kite login")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

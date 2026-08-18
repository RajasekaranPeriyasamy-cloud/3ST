"""get_chain memoisation — invalidation on the instruments dump, and copy safety.

The pandas scan behind get_chain cost 0.4-1.0s and ran on every poll of every
desk that touches a chain (~19 call sites). It is now memoised against the
instruments-cache mtime, which makes two properties load-bearing: the cache
must drop when the dump is refreshed, and callers must never be handed the
cached object itself.
"""

from __future__ import annotations

import pandas as pd
import pytest

from options import chain as chain_mod


@pytest.fixture
def instruments_file(tmp_path, monkeypatch):
    """Point chain.py's own CACHE_FILE reference at a writable stand-in.

    chain.py does `from instruments import CACHE_FILE` at import time, so the
    module under test holds its own reference -- patching `instruments.CACHE_FILE`
    would leave this pointed at the real data/ dump.
    """
    f = tmp_path / "kite_instruments.json"
    f.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(chain_mod, "CACHE_FILE", f)
    return f


@pytest.fixture
def counted_df(monkeypatch):
    """Stub the pandas scan and count how often it actually runs."""
    calls = {"n": 0}

    def _fake(underlying: str) -> pd.DataFrame:
        calls["n"] += 1
        return pd.DataFrame(
            [
                {
                    "strike": 24000.0,
                    "instrument_type": "CE",
                    "tradingsymbol": "NIFTY26AUG24000CE",
                    "instrument_token": 1,
                    "exchange": "NFO",
                    "lot_size": 75,
                    "expiry": pd.Timestamp("2026-08-27"),
                },
                {
                    "strike": 24000.0,
                    "instrument_type": "PE",
                    "tradingsymbol": "NIFTY26AUG24000PE",
                    "instrument_token": 2,
                    "exchange": "NFO",
                    "lot_size": 75,
                    "expiry": pd.Timestamp("2026-08-27"),
                },
            ]
        )

    monkeypatch.setattr(chain_mod, "_underlying_options_df", _fake)
    chain_mod._CHAIN_CACHE.clear()
    yield calls
    chain_mod._CHAIN_CACHE.clear()


def test_second_call_is_served_from_cache(instruments_file, counted_df):
    a = chain_mod.get_chain("NIFTY", "2026-08-27")
    b = chain_mod.get_chain("NIFTY", "2026-08-27")
    assert counted_df["n"] == 1
    assert a == b
    assert len(a["strikes"]) == 1
    assert a["strikes"][0]["ce"]["tradingsymbol"] == "NIFTY26AUG24000CE"


def test_distinct_expiries_do_not_share_an_entry(instruments_file, counted_df):
    chain_mod.get_chain("NIFTY", "2026-08-27")
    chain_mod.get_chain("NIFTY", "2026-09-03")
    assert counted_df["n"] == 2


def test_refreshed_instruments_dump_invalidates(instruments_file, counted_df):
    chain_mod.get_chain("NIFTY", "2026-08-27")
    import os

    st = instruments_file.stat()
    os.utime(instruments_file, (st.st_atime + 60, st.st_mtime + 60))
    chain_mod.get_chain("NIFTY", "2026-08-27")
    assert counted_df["n"] == 2, "a refreshed instruments dump must rebuild the chain"


def test_caller_mutation_cannot_poison_the_cache(instruments_file, counted_df):
    first = chain_mod.get_chain("NIFTY", "2026-08-27")
    first["strikes"][0]["ce"]["tradingsymbol"] = "MUTATED"
    first["strikes"].append({"strike": 99999.0})
    first["underlying"] = "TAMPERED"

    second = chain_mod.get_chain("NIFTY", "2026-08-27")
    assert second["underlying"] == "NIFTY"
    assert len(second["strikes"]) == 1
    assert second["strikes"][0]["ce"]["tradingsymbol"] == "NIFTY26AUG24000CE"


def test_empty_underlying_frame_is_cached_without_raising(instruments_file, monkeypatch):
    monkeypatch.setattr(chain_mod, "_underlying_options_df", lambda u: pd.DataFrame())
    chain_mod._CHAIN_CACHE.clear()
    out = chain_mod.get_chain("NIFTY", "2026-08-27")
    assert out["strikes"] == []
    chain_mod._CHAIN_CACHE.clear()

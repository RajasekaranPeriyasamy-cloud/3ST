"""End-to-end causality test: the future must not touch the past.

Everything else in the suite checks a component. This checks the assembled
pipeline -- resample, cointegration screen, FDR, MLE, filter, state machine,
execution lag, cost model -- with one blunt question: if I change prices from
some date onward, does anything before that date move?

It is the test that would have caught the reference article's real problem.
Its ``find_pairs`` runs ``coint()`` over the whole price frame and then trades
that same frame, so every pair in the book was chosen using data from the future
of the trades it is credited with. No component test finds that; only a
perturbation test on the whole pipeline does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kpairs import tfstudy  # noqa: E402
from kpairs.backtest import CostModel  # noqa: E402


def _synthetic_index_panel(n_sessions: int = 900, seed: int = 3) -> pd.DataFrame:
    """Three cointegrated 'indices' on a real NSE 5-minute session grid."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2019-01-01", periods=n_sessions)
    idx = pd.DatetimeIndex(np.concatenate(
        [pd.date_range(f"{d:%Y-%m-%d} 09:15", periods=75, freq="5min") for d in days]))
    n = len(idx)

    market = np.cumsum(rng.normal(0, 0.0009, n))
    out = {}
    for i, name in enumerate(["NIFTY", "BANKNIFTY", "FINNIFTY"]):
        ar = np.zeros(n)
        for t in range(1, n):
            ar[t] = 0.999 * ar[t - 1] + rng.normal(0, 0.0004)
        lp = np.log(10000 * (i + 1)) + (0.9 + 0.15 * i) * market + ar
        out[name] = np.exp(lp)
    return pd.DataFrame(out, index=idx)


def _cfg() -> tfstudy.TFConfig:
    return tfstudy.TFConfig(
        formation_sessions=250, trading_sessions=60, max_pairs=3,
        coint_p_max=0.50, use_fdr=False, hl_max_sessions=40.0,
        max_hurst=1.0, min_corr=0.0,
        costs=CostModel(bps_per_turnover=1.5, roll_bps_per_month=1.5),
    )


@pytest.mark.parametrize("timeframe", ["15m", "60m"])
def test_future_prices_cannot_change_past_returns(timeframe):
    px5 = _synthetic_index_panel()
    cut = px5.index[int(len(px5) * 0.65)]

    base = tfstudy.run_grid(px5, timeframe, {"k": _cfg()})["k"]
    assert not base.daily.empty, "need a non-trivial baseline to compare against"

    # Detonate everything after the cut: a large, permanent, pair-specific shift.
    tampered = px5.copy()
    tampered.loc[tampered.index > cut, "BANKNIFTY"] *= 1.35
    tampered.loc[tampered.index > cut, "FINNIFTY"] *= 0.80

    after = tfstudy.run_grid(tampered, timeframe, {"k": _cfg()})["k"]

    a = base.daily["ret_net"]
    b = after.daily["ret_net"]
    common = a.index.intersection(b.index)
    pre = common[common <= cut]
    assert len(pre) > 200, "not enough pre-cut bars to make the test meaningful"

    np.testing.assert_allclose(
        a.loc[pre].to_numpy(), b.loc[pre].to_numpy(), rtol=1e-10, atol=1e-14,
        err_msg=f"{timeframe}: returns before {cut} moved when the future changed",
    )

    # ...and the tamper must actually have done something after the cut,
    # otherwise the test above passes vacuously.
    post = common[common > cut]
    if len(post) > 50:
        assert not np.allclose(a.loc[post].to_numpy(), b.loc[post].to_numpy()), \
            "tamper had no effect at all -- the test is not exercising anything"


def test_selection_only_sees_the_formation_window():
    """A narrower version of the same claim, aimed at the screen itself."""
    px5 = _synthetic_index_panel(n_sessions=700)
    px = tfstudy.B.resample(px5, "60m")
    bps = len(px) / pd.DatetimeIndex(px.index).normalize().nunique()
    form = px.iloc[:int(250 * bps)]

    sel_a = tfstudy._select(form, _cfg(), bps)

    tampered = px.copy()
    tampered.iloc[int(250 * bps):] *= 2.0        # obliterate everything after
    sel_b = tfstudy._select(tampered.iloc[:int(250 * bps)], _cfg(), bps)

    pd.testing.assert_frame_equal(sel_a, sel_b)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

"""Turn a causal z-score into a position path.

The reference article's version of this is worth quoting because the bug in it
changes the strategy into a different strategy:

    signal[z < -entry] = 1
    signal[z > entry] = -1
    signal[(z.abs() < exit_z) & (signal.shift(1) != 0)] = 0
    signal = signal.replace(0, np.nan).ffill().fillna(0)

The last line overwrites every zero -- including the exits the line above just
wrote -- with the previous non-zero position. The book can never go flat. It
holds a full-size position from the first entry to the end of the sample,
flipping only when the *opposite* entry threshold trips. Whatever that is, it
is not the mean-reversion strategy the article describes, and it is not what
the reported Sharpe would be measuring.

(There is a second bug stacked on the first: ``signal.shift(1)`` is evaluated
on the pre-fill series, which is zero almost everywhere, so the exit condition
barely fires even before the fill erases it.)

A position path with entry/exit hysteresis is a state machine. Vectorised
boolean assignment cannot express one, because the action at bar t depends on
the position at bar t-1, which depends on the action at t-1. Write the loop.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def positions_from_z(
    z: np.ndarray,
    *,
    entry: float = 2.0,
    exit_: float = 0.5,
    stop: float = 4.0,
    max_hold: int = 60,
    exec_lag: int = 1,
    cooldown: int = 5,
    force_flat: np.ndarray | None = None,
    no_entry: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Entry/exit/stop state machine over a z-score path.

    Convention: ``+1`` is *long the spread* -- long Y, short beta of X -- taken
    when z is very negative (Y cheap relative to X).

    Parameters
    ----------
    entry, exit_
        Enter at ``|z| > entry``, flatten when ``|z| < exit_``. The gap between
        them is the hysteresis band; without it the book churns every time z
        crosses a single threshold.
    stop
        Flatten at ``|z| > stop`` -- the relationship is not reverting, it is
        breaking. Note this is a *widening* stop, the opposite of a price stop:
        the trade is stopped out precisely when it looks most attractive on the
        signal. Without it, a structural break (a demerger, a fraud, a rating
        downgrade) is an unbounded loss, because the signal keeps saying "add".
    cooldown
        Bars to stay out after a stop, *and* a hard re-arm condition on top:
        the pair cannot be re-entered until ``|z|`` comes back inside ``entry``.
        Without this the stop is decorative -- you flatten at z = -4.2 and the
        very next bar, with z still at -4.1, the entry rule fires again and you
        are back in the same broken trade at a worse level. A stop that
        immediately re-enters is not a stop.
    max_hold
        Time stop in bars. A pair whose half-life is 12 days and which has been
        open 60 days has already told you the reversion is not coming.
    exec_lag
        Bars between the signal firing and the position existing. ``1`` means
        the z computed at this bar's close is traded at the next bar's close,
        which is the honest assumption for a bar-close system. ``0`` assumes
        you can transact at the close you are still computing from -- at 5
        minutes that is the difference between a strategy and a fantasy.
    force_flat
        Boolean mask of bars on which the book must be flat regardless of the
        signal, and on which no new position may be opened. Pass
        ``bars.session_boundaries(index)`` to run intraday-only, squaring off
        before the close. This matters more than it looks: at 5-minute
        frequency an overnight gap is 10-20x a typical bar's move, so a
        strategy that carries positions overnight is a different strategy,
        earning (or paying) most of its P&L in bars it never traded. Compare
        both -- do not assume.
    no_entry
        Boolean mask of bars whose z may not *open* a position, though an
        existing one may still be held or closed. Pass
        ``bars.session_opens(index)`` to refuse entries triggered by the first
        bar of the day: that bar's innovation contains the whole overnight gap,
        which the filter prices with its ordinary intraday variance, so its z is
        typically 2-3x too large. A mean-reversion rule fires on large |z|, so
        without this the book is systematically entering on the one bar the
        model understands least.

    Returns
    -------
    dict with ``target`` (pre-lag decision) and ``position`` (post-lag holding).
    """
    z = np.asarray(z, dtype=float)
    n = z.size
    target = np.zeros(n)
    flat_mask = (np.zeros(n, dtype=bool) if force_flat is None
                 else np.asarray(force_flat, dtype=bool))
    if flat_mask.size != n:
        raise ValueError(f"force_flat length {flat_mask.size} != z length {n}")
    no_entry_mask = (np.zeros(n, dtype=bool) if no_entry is None
                     else np.asarray(no_entry, dtype=bool))
    if no_entry_mask.size != n:
        raise ValueError(f"no_entry length {no_entry_mask.size} != z length {n}")

    # The mask says which bars the *position* must be flat on, but the state
    # machine produces the *decision* series, which the execution lag then
    # shifts forward. Constraining the decision at bar b leaves the position at
    # bar b+lag still holding -- i.e. the book carries through the close it was
    # supposed to square off before. Shift the constraint back by the lag so it
    # lands where it was meant to.
    if exec_lag > 0 and flat_mask.any():
        decide_flat = np.zeros(n, dtype=bool)
        decide_flat[:n - exec_lag] = flat_mask[exec_lag:]
        decide_flat[n - exec_lag:] = True      # nothing left to unwind into
        flat_mask = decide_flat

    pos = 0.0
    held = 0
    blocked = 0        # bars remaining in the post-stop cooldown
    disarmed = False   # True until |z| comes back inside the entry band
    for t in range(n):
        zt = z[t]
        if not np.isfinite(zt):
            target[t] = pos
            if pos != 0.0:
                held += 1
            continue

        if flat_mask[t]:
            # Square-off bar: close anything open, open nothing.
            pos, held = 0.0, 0
            target[t] = 0.0
            continue

        if pos == 0.0:
            if blocked > 0:
                blocked -= 1
            if disarmed and abs(zt) < entry:
                disarmed = False
            if blocked == 0 and not disarmed and not no_entry_mask[t]:
                if zt <= -entry:
                    pos, held = 1.0, 0
                elif zt >= entry:
                    pos, held = -1.0, 0
        else:
            held += 1
            if abs(zt) >= stop:
                pos, held = 0.0, 0          # structural-break stop
                blocked, disarmed = cooldown, True
            elif abs(zt) <= exit_:
                pos, held = 0.0, 0          # reverted, take it
            elif held >= max_hold:
                pos, held = 0.0, 0          # time stop
                blocked, disarmed = cooldown, True
        target[t] = pos

    if exec_lag > 0:
        position = np.concatenate([np.zeros(exec_lag), target[:-exec_lag]])
    else:
        position = target.copy()

    return {"target": target, "position": position}


def rolling_z(resid: np.ndarray, window: int = 60, min_periods: int = 30) -> np.ndarray:
    """Rolling z-score of a residual series -- for the OLS baseline only.

    Strictly causal: the mean and sd at bar t use bars ``[t-window, t-1]``,
    excluding t itself, so the point being scored is not part of its own
    normalisation.

    The Kalman path does not need this function at all: ``e_t / sqrt(Q_t)`` is
    already standardised by the filter's own forecast variance. That is a real
    advantage and not just tidiness -- a rolling sd is itself a noisy estimate
    that spikes right after a shock, mechanically suppressing z exactly when
    the spread has widened most.
    """
    s = pd.Series(resid, dtype=float)
    mu = s.shift(1).rolling(window, min_periods=min_periods).mean()
    sd = s.shift(1).rolling(window, min_periods=min_periods).std(ddof=1)
    out = (s - mu) / sd
    return out.to_numpy()

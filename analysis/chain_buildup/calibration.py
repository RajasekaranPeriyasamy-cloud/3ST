"""Fitted breach thresholds for the Chain Build-Up desk — GENERATED FILE.

Regenerate with ``python scripts/fit_chain_buildup_thresholds.py``. Do not
hand-edit: the next fit overwrites it. See that script's docstring for why the
model is factorised rather than a full cross-tab, and what it therefore cannot
represent.

Fitted from 42 archived session-files (NIFTY, BANKNIFTY, SENSEX) at the 95th percentile.
"""

from __future__ import annotations

TARGET_PERCENTILE = 95.0
FITTED_SESSIONS = 42
FITTED_UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'SENSEX']

#: Overall p95 of |dOI %| per timeframe — the level the factors scale.
BASE_P95: dict[int, float] = {5: 9.479, 15: 20.776, 30: 33.33, 60: 46.673}

#: Multiplier by days-to-expiry bucket. The dominant conditioning variable:
#: expiry-day OI churns several times harder than a far-dated month.
DTE_FACTOR: dict[int, dict[str, float]] = {
    5: {
        '0-1': 1.6896,
        '2-7': 0.9051,
        '22+': 0.4069,
        '8-21': 0.8619,
    },
    15: {
        '0-1': 1.6243,
        '2-7': 0.9338,
        '22+': 0.4075,
        '8-21': 0.9025,
    },
    30: {
        '0-1': 1.4745,
        '2-7': 0.9453,
        '22+': 0.425,
        '8-21': 0.9167,
    },
    60: {
        '0-1': 1.4982,
        '2-7': 0.8849,
        '22+': 0.4643,
        '8-21': 1.038,
    },
}

#: Multiplier by bucket-close time. Re-centred to mean 1.0, so it
#: redistributes strictness within a session without changing its level.
TOD_FACTOR: dict[int, dict[str, float]] = {
    5: {
        '09:25': 3.6946,
        '09:30': 3.9299,
        '09:35': 2.4659,
        '09:40': 1.9116,
        '09:45': 1.4928,
        '09:50': 1.5361,
        '09:55': 1.1054,
        '10:00': 0.8677,
        '10:05': 1.1172,
        '10:10': 0.9971,
        '10:15': 0.9741,
        '10:20': 1.2096,
        '10:25': 1.315,
        '10:30': 0.8173,
        '10:35': 1.0735,
        '10:40': 0.9671,
        '10:45': 0.6278,
        '10:50': 1.0689,
        '10:55': 1.1627,
        '11:00': 0.8755,
        '11:05': 0.8694,
        '11:10': 1.1597,
        '11:15': 0.8176,
        '11:20': 0.7413,
        '11:25': 0.9219,
        '11:30': 0.7881,
        '11:35': 1.2123,
        '11:40': 0.5984,
        '11:45': 0.8167,
        '11:50': 0.6999,
        '11:55': 0.955,
        '12:00': 1.12,
        '12:05': 0.9911,
        '12:10': 0.6481,
        '12:15': 0.4505,
        '12:20': 0.7941,
        '12:25': 0.5642,
        '12:30': 0.5029,
        '12:35': 0.4926,
        '12:40': 0.5753,
        '12:45': 0.5235,
        '12:50': 0.8255,
        '12:55': 1.0197,
        '13:00': 0.6279,
        '13:05': 0.7802,
        '13:10': 0.7696,
        '13:15': 0.6483,
        '13:20': 0.6119,
        '13:25': 0.6189,
        '13:30': 0.6599,
        '13:35': 0.5145,
        '13:40': 0.5632,
        '13:45': 0.593,
        '13:50': 1.0068,
        '13:55': 0.5412,
        '14:00': 0.9846,
        '14:05': 0.6949,
        '14:10': 0.7068,
        '14:15': 0.6104,
        '14:20': 0.5392,
        '14:25': 0.6553,
        '14:30': 0.566,
        '14:35': 1.1871,
        '14:40': 1.3649,
        '14:45': 1.5055,
        '14:50': 0.7268,
        '14:55': 0.8672,
        '15:00': 0.8422,
        '15:05': 1.0116,
        '15:10': 0.8886,
        '15:15': 0.9363,
        '15:20': 1.0878,
        '15:25': 1.2662,
        '15:30': 1.686,
        '15:35': 1.5265,
        '15:40': 1.3638,
        '15:45': 0.7494,
    },
    15: {
        '09:45': 2.5028,
        '10:00': 1.4636,
        '10:15': 1.25,
        '10:30': 1.2779,
        '10:45': 0.9878,
        '11:00': 1.1794,
        '11:15': 1.0141,
        '11:30': 0.8857,
        '11:45': 0.8851,
        '12:00': 0.9283,
        '12:15': 0.7009,
        '12:30': 0.6487,
        '12:45': 0.5406,
        '13:00': 0.8539,
        '13:15': 0.8327,
        '13:30': 0.6339,
        '13:45': 0.494,
        '14:00': 0.8384,
        '14:15': 0.6317,
        '14:30': 0.5829,
        '14:45': 1.3697,
        '15:00': 0.8731,
        '15:15': 0.9155,
        '15:30': 1.4659,
        '15:45': 1.2433,
    },
    30: {
        '10:15': 1.5283,
        '10:45': 1.277,
        '11:15': 1.2456,
        '11:45': 0.9407,
        '12:15': 0.9147,
        '12:45': 0.613,
        '13:15': 0.9663,
        '13:45': 0.5839,
        '14:15': 0.673,
        '14:45': 0.9604,
        '15:15': 0.9404,
        '15:45': 1.3567,
    },
    60: {
        '11:15': 1.4084,
        '12:15': 1.088,
        '13:15': 0.921,
        '14:15': 0.6647,
        '15:15': 1.0219,
        '16:15': 0.8961,
    },
}


def adaptive_threshold(timeframe_min: int, dte_bucket: str, bucket_key: str) -> float | None:
    """Fitted |dOI %| threshold, or None when this timeframe was never fitted."""
    base = BASE_P95.get(timeframe_min)
    if base is None:
        return None
    dte = DTE_FACTOR.get(timeframe_min, {}).get(dte_bucket, 1.0)
    tod = TOD_FACTOR.get(timeframe_min, {}).get(bucket_key, 1.0)
    return base * dte * tod

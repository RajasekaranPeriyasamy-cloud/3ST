"""Add cloned trading-algo repo to import path."""

from __future__ import annotations

import sys
from pathlib import Path

_TRADING_ALGO = Path(__file__).resolve().parents[2] / "trading-algo"


def trading_algo_root() -> Path:
    return _TRADING_ALGO


def ensure_trading_algo_path() -> Path:
    root = _TRADING_ALGO
    if not root.is_dir():
        raise RuntimeError(
            f"trading-algo not found at {root}. "
            "Clone or download Raahi-Bhushan/trading-algo to Desktop/trading-algo."
        )
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    return root

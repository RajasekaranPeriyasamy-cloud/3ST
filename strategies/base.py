"""Strategy plugin contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import pandas as pd

Side = Literal["long", "short", "flat"]


@dataclass
class Signal:
    action: Literal["enter_long", "enter_short", "exit"]
    reason: str = ""
    price: float | None = None


class Strategy(Protocol):
    name: str

    def on_bar(self, df: pd.DataFrame) -> Signal | None:
        """Called with history ending at the latest closed bar."""
        ...

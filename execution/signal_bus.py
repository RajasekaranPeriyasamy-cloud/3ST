"""Signal intents — the message a strategy emits when it wants to change a leg.

Part of the Phase 3 execution-architecture plan (see
docs/CONVERSATION_SUMMARY.md, "Execution architecture — phase reminders"):
strategies stop calling the broker directly and instead emit a ``SignalIntent``
describing what they want; ``execution/order_router.py`` is the only thing that
turns an intent into an order + a ``execution/position_ledger.py`` row.

This module has no broker/risk/Kite imports (only a lazy import of
``order_tag`` for tagging) so it stays trivially unit-testable and reusable by
any runner — Rolling Straddle, Watchlist, Premium Book, Survivor, Wave —
without pulling in execution side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

IntentKind = Literal["enter", "exit", "adjust"]


@dataclass(frozen=True)
class SignalIntent:
    """One strategy's request to change a leg's position.

    ``leg_key`` is the owner's own local name for the leg (e.g. ``"ce"``,
    ``"pe"``, a watchlist item id). Combined with ``owner`` + ``instance_id``
    it forms the ledger's stable id, so the same logical leg always maps to
    the same ledger row across restarts — no re-deriving identity from
    whatever happens to be in a state file that day.

    Two ways to describe the desired order:

    - Fixed order: set ``transaction_type`` ("BUY"/"SELL") + ``quantity``.
    - Target order: set ``target_qty`` (signed net target); the router works
      out BUY/SELL + quantity from the broker's current net position via
      ``broker.execution_support.plan_order_to_target`` — same "smart order"
      behavior ``order_executor.place_leg_to_target`` already has today.
    """

    owner: str
    instance_id: str
    leg_key: str
    kind: IntentKind
    exchange: str
    tradingsymbol: str
    product: str = "MIS"
    transaction_type: str | None = None  # "BUY" | "SELL" — for fixed orders
    quantity: int | None = None  # for fixed orders
    target_qty: int | None = None  # for target orders (None = not a target order)
    order_type: str = "MARKET"
    price: float | None = None
    reason: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.target_qty is None and (not self.transaction_type or not self.quantity):
            raise ValueError(
                "SignalIntent needs either target_qty, or transaction_type + quantity"
            )

    @property
    def leg_id(self) -> str:
        """Stable ledger key: same owner+instance+leg always maps to one row."""
        return f"{self.owner}:{self.instance_id}:{self.leg_key}"

    def tag(self) -> str:
        """Idempotent daily tag for *this* intent — same convention as
        ``order_executor.order_tag`` (``3ST-{LEG_KEY}-{YYYYMMDD}-{kind}``,
        truncated to Kite's 20-char limit). An "enter" and the "exit" that
        later closes the same leg get different tags (``-entry`` vs
        ``-exit``) even though they share a ``leg_id``.
        """
        from execution.order_executor import order_tag

        tag_kind = "entry" if self.kind == "enter" else self.kind
        return order_tag(self.leg_key, tag_kind)

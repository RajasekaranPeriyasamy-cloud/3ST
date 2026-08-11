"""Equity Research Desk (`/equity-report`).

Re-hosts the `india-equity-report` Claude skill as a server-side Anthropic API
agent so the desk can generate NSE/BSE Buy/Sell/Hold research reports without a
chat session.

Deliberately isolated from the trading side: nothing in this package imports
from ``broker/``, ``execution/``, or ``risk/``, and it places no orders. A slow
or failing model call must never be able to delay an order-placing tick.
"""

from __future__ import annotations

"""Option-to-option arbitrage scanner.

Scan-and-alert only. This package imports nothing from ``broker/``,
``execution/`` or ``risk/`` — it never places an order. Wiring a detected
edge to ``execution/order_router.submit_intent()`` is a separate,
operator-approved change (see docs/OPTION_ARBITRAGE.md).
"""

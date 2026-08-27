"""Arbitrage detectors, one module per violation family.

Every detector returns rows shaped the same way so the scanner can rank them
together:

``family``      which check fired
``legs``        the exact orders, priced at bid/ask — never at LTP
``gross``       rupee edge before charges
``cost``        charges from :mod:`analysis.opt_arb.costs`
``net``         ``gross - cost`` — the only number worth looking at
``max_lots``    how many the top of book actually supports
``tier``        ``"A"`` model-free arbitrage, ``"B"`` spread with a real driver
``warnings``    anything that makes the row less tradable than it looks
"""

from analysis.opt_arb.detectors import box, butterfly, vertical, xcontract

__all__ = ["box", "butterfly", "vertical", "xcontract"]

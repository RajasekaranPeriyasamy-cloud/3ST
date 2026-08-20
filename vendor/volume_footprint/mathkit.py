"""Numerical primitives used by the Volume Footprint port.

The original Pine script deliberately uses *two* different normal CDFs:

* ``f_normCdf``     - Abramowitz & Stegun 26.2.17, |error| < 7.5e-8.
                      Used for the per-bar footprint rows (the table), where
                      thousands of evaluations happen per redraw and 8 digits
                      is far beyond what a volume cell can show.
* ``f_profNormCdf`` - Cody's rational Chebyshev ``erfc``, |error| < 2e-16.
                      Used for the volume profile, because the profile runs a
                      residual self-check in *parts per million*: an 7.5e-8
                      CDF would light up the DRIFT warning on its own.

Both are reproduced here so the Python output matches the Pine output digit
for digit in the place each one is used. ``norm_cdf_precise`` delegates to
``math.erfc``, which is the same Cody-class algorithm shipped by libm.
"""

from __future__ import annotations

import math

__all__ = [
    "norm_cdf_fast",
    "norm_cdf_precise",
    "norm_pdf",
    "INV_SQRT_2",
    "INV_SQRT_2PI",
]

INV_SQRT_2 = 7.071067811865476e-1     # correctly rounded 1/sqrt(2)
INV_SQRT_2PI = 3.989422804014327e-1   # correctly rounded 1/sqrt(2*pi)

# Abramowitz & Stegun 26.2.17 coefficients.
_AS_T = 0.2316419
_AS_C = (0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429)


def norm_cdf_fast(z: float) -> float:
    """Phi(z) via Abramowitz & Stegun 26.2.17. Absolute error < 7.5e-8.

    Port of Pine ``f_normCdf``. Clamped to [0, 1] exactly as the original,
    so a row mass can never come out negative from rounding.
    """
    x = abs(z)
    t = 1.0 / (1.0 + _AS_T * x)
    c0, c1, c2, c3, c4 = _AS_C
    poly = t * (c0 + t * (c1 + t * (c2 + t * (c3 + t * c4))))
    phi = INV_SQRT_2PI * math.exp(-0.5 * x * x)
    cdf = 1.0 - phi * poly if z >= 0.0 else phi * poly
    return max(0.0, min(1.0, cdf))


def norm_cdf_precise(z: float) -> float:
    """Phi(z) = erfc(-z/sqrt(2)) / 2. Absolute error < 2e-16.

    Port of Pine ``f_profNormCdf``. Pine hand-rolls Cody's rational Chebyshev
    ``erfc`` because Pine has no ``erfc``; CPython's ``math.erfc`` is the same
    algorithm class out of libm, so we call it directly.
    """
    return 0.5 * math.erfc(-z * INV_SQRT_2)


def norm_pdf(z: float) -> float:
    """phi(z) = exp(-z^2/2) / sqrt(2*pi). Port of Pine ``f_profNormPdf``."""
    return INV_SQRT_2PI * math.exp(-0.5 * z * z)

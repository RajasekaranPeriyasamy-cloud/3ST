"""Concentration-penalised allocation as a QUBO — research only, NOT part of the desk.

This file exists because Part II of the HHI methodology audit was asked for and
is worth having written down. It is deliberately quarantined:

  * ``research/`` is not imported by ``api/``, ``options/``, ``execution/`` or
    ``broker/``. Nothing in the running desk touches it.
  * It imports nothing from the project. Run it standalone.
  * ``qiskit`` is NOT in ``requirements.txt`` and should not be added on this
    file's account. It degrades to a clear message if absent.

**The honest verdict from the audit, restated so it travels with the code:** at
41 strikes there is no bottleneck for this to solve. HHI is an O(N) sum; the
continuous relaxation of the problem below is convex and CVXPY solves it exactly
in milliseconds. The live book at N=41, B=4 would need 164 qubits, against a
state-vector simulation ceiling near 30. A crossover would require N in the high
hundreds with dense coupling — roughly two orders of magnitude larger than this
desk runs. Keep the exact baseline in the loop and read the gap.

**Why the mapping is still interesting.** Under the budget constraint
``sum(w) = 1``, HHI *is* ``w'w`` — already a pure quadratic form, no
linearisation and no slack variables. Concentration is one of the few risk
functionals that lands in QUBO form without distortion::

    min  -mu'w  +  gamma * w'Sigma w  +  lambda * w'w  +  rho * (1'w - 1)^2
                                        ^^^^^^^^^^^^^
                                        this term is HHI, at feasibility

Discretise ``w_i`` with a B-bit expansion so ``w = A z``, ``z`` binary, and every
term is quadratic in ``z``.

**Caveat that matters:** ``lambda * w'w`` equals HHI only *at* feasibility. Under
a soft penalty the optimiser can trade budget violation against apparent
concentration, so always check ``1'w ~= 1`` on the returned solution before
reading the HHI term. ``main()`` prints it for exactly that reason.

Usage::

    pip install "qiskit~=1.0" qiskit-algorithms qiskit-optimization
    python research/hhi_qubo_demo.py
"""

from __future__ import annotations

import sys

import numpy as np

# Bits per weight. Qubit count is N * B — the whole scaling story in one line.
BITS_PER_WEIGHT = 3


def build_qubo(mu, sigma, *, bits=BITS_PER_WEIGHT, gamma=1.0, lam=2.0, rho=10.0):
    """Return (QuadraticProgram, A) with ``w = A @ z``.

    ``lam`` is the HHI penalty weight; raising it buys a flatter allocation.
    """
    from qiskit_optimization import QuadraticProgram

    n = len(mu)
    qp = QuadraticProgram("hhi_penalised_allocation")
    for i in range(n):
        for b in range(bits):
            qp.binary_var(f"z_{i}_{b}")

    scale = 1.0 / (2**bits - 1)
    a = np.zeros((n, n * bits))
    for i in range(n):
        for b in range(bits):
            a[i, i * bits + b] = (2**b) * scale

    ones = np.ones(n) @ a
    quad = a.T @ (gamma * sigma + lam * np.eye(n)) @ a + rho * np.outer(ones, ones)
    lin = -(mu @ a) - 2.0 * rho * ones
    qp.minimize(linear=lin, quadratic=quad)
    return qp, a


def main() -> int:
    try:
        from qiskit.primitives import Sampler
        from qiskit_algorithms import QAOA, NumPyMinimumEigensolver
        from qiskit_algorithms.optimizers import COBYLA
        from qiskit_optimization.algorithms import MinimumEigenOptimizer
    except ImportError as exc:
        print(f"qiskit stack not installed ({exc}).")
        print('  pip install "qiskit~=1.0" qiskit-algorithms qiskit-optimization')
        print("  Deliberately absent from requirements.txt — this is research only.")
        return 1

    rng = np.random.default_rng(7)
    n = 6  # 6 strikes x 3 bits = 18 qubits. Raise carefully; cost is exponential.
    mu = rng.normal(0.02, 0.01, n)
    sigma = np.diag(rng.uniform(0.01, 0.05, n))

    qp, a = build_qubo(mu, sigma)
    print(f"qubits: {qp.get_num_binary_vars()}  (N={n} x B={BITS_PER_WEIGHT})")

    qaoa = QAOA(sampler=Sampler(), optimizer=COBYLA(maxiter=300), reps=3)
    res = MinimumEigenOptimizer(qaoa).solve(qp)
    w = a @ res.x

    # Feasibility first — the HHI reading below is meaningless without it.
    budget = float(w.sum())
    print(f"budget 1'w = {budget:.4f}  {'OK' if abs(budget - 1) < 0.05 else 'INFEASIBLE'}")
    print(f"HHI    w'w = {float(w @ w):.4f}")
    print(f"objective  = {res.fval:.6f}")

    # The exact baseline is not optional. At this size it is ground truth, and
    # the gap is the only honest way to report what QAOA actually achieved.
    exact = MinimumEigenOptimizer(NumPyMinimumEigensolver()).solve(qp)
    print(f"exact      = {exact.fval:.6f}")
    print(f"QAOA gap   = {res.fval - exact.fval:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Trade ideas from the complementary pricing desk (BS edge).

Read-only suggestions — never arms, orders, or mutates 3ST state.
edge = LTP − BS fair (flat ATM IV): + rich (prefer sell), − cheap (prefer buy).
"""

from __future__ import annotations

from typing import Any

from config import INDEX_OPTIONS, PRICING_ENGINE_DEFAULTS


def _cfg() -> dict[str, Any]:
    return dict(PRICING_ENGINE_DEFAULTS.get("recommendations") or {})


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    return x


def _leg(row: dict[str, Any], side: str) -> dict[str, Any] | None:
    block = row.get(side)
    if not isinstance(block, dict):
        return None
    ltp = _f(block.get("ltp"))
    edge = _f(block.get("edge"))
    fair = _f(block.get("bs_fair"))
    if ltp is None or ltp <= 0 or edge is None:
        return None
    return {
        "strike": float(row["strike"]),
        "ltp": round(ltp, 4),
        "edge": round(edge, 4),
        "bs_fair": round(fair, 4) if fair is not None else None,
        "heston_edge": _f(block.get("heston_edge")),
    }


def _in_window(strike: float, atm: float, step: float, steps: int) -> bool:
    return abs(strike - atm) <= steps * step + 1e-6


def _bull_put_credit(
    by_strike: dict[float, dict[str, Any]],
    *,
    atm: float,
    step: float,
    steps: int,
    min_ltp: float,
    lot: int,
) -> dict[str, Any] | None:
    """Sell richer PE / buy lower PE (1 step)."""
    candidates: list[dict[str, Any]] = []
    for k, row in by_strike.items():
        if not _in_window(k, atm, step, steps):
            continue
        pe = _leg(row, "pe")
        if not pe or pe["ltp"] < min_ltp:
            continue
        long_k = k - step
        if long_k not in by_strike:
            continue
        pe_long = _leg(by_strike[long_k], "pe")
        if not pe_long or pe_long["ltp"] < min_ltp:
            continue
        credit = round(pe["ltp"] - pe_long["ltp"], 4)
        if credit <= 0 or credit >= step:
            continue
        fair_credit = None
        if pe.get("bs_fair") is not None and pe_long.get("bs_fair") is not None:
            fair_credit = round(float(pe["bs_fair"]) - float(pe_long["bs_fair"]), 4)
        edge_vs_fair = (
            round(credit - fair_credit, 4) if fair_credit is not None else None
        )
        # Prefer rich short and positive edge vs fair
        score = pe["edge"] + max(0.0, pe["edge"] - pe_long["edge"])
        if edge_vs_fair is not None:
            score += max(0.0, edge_vs_fair)
        heston_ok = None
        if pe.get("heston_edge") is not None:
            heston_ok = pe["heston_edge"] > 0  # still rich vs Heston
        candidates.append(
            {
                "score": score,
                "short": pe,
                "long": pe_long,
                "credit": credit,
                "fair_credit": fair_credit,
                "edge_vs_fair": edge_vs_fair,
                "heston_agrees": heston_ok,
            }
        )
    if not candidates:
        return None
    best = max(candidates, key=lambda c: c["score"])
    width = float(step)
    credit = best["credit"]
    max_profit = credit
    max_loss = round(width - credit, 4)
    be = round(best["short"]["strike"] - credit, 4)
    heston_note = ""
    if best["heston_agrees"] is True:
        heston_note = " Heston also flags the short put as rich."
    elif best["heston_agrees"] is False:
        heston_note = " Heston disagrees on richness — treat with caution."

    fair_txt = ""
    if best["fair_credit"] is not None and best["edge_vs_fair"] is not None:
        fair_txt = (
            f" BS-fair credit ≈ {best['fair_credit']:.2f}; market credit is "
            f"{best['edge_vs_fair']:+.2f} pts vs fair."
        )

    reasoning = (
        f"Sell PE {best['short']['strike']:.0f} (edge {best['short']['edge']:+.2f}, rich) / "
        f"Buy PE {best['long']['strike']:.0f} (edge {best['long']['edge']:+.2f}). "
        f"Collect {credit:.2f} credit; max loss {max_loss:.2f}; breakeven {be:.2f}."
        f"{fair_txt}{heston_note}"
    )

    return {
        "id": "bull_put_credit",
        "structure": "bull_put_credit",
        "title": (
            f"Bull put credit — Sell PE {best['short']['strike']:.0f} / "
            f"Buy PE {best['long']['strike']:.0f}"
        ),
        "bias": "bullish_to_neutral",
        "action": "credit",
        "legs": [
            {
                "side": "sell",
                "option_type": "PE",
                "strike": best["short"]["strike"],
                "ltp": best["short"]["ltp"],
                "edge": best["short"]["edge"],
            },
            {
                "side": "buy",
                "option_type": "PE",
                "strike": best["long"]["strike"],
                "ltp": best["long"]["ltp"],
                "edge": best["long"]["edge"],
            },
        ],
        "net_premium": credit,
        "width": width,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "breakeven": be,
        "lot_size": lot,
        "net_premium_inr": round(credit * lot, 2),
        "max_profit_inr": round(max_profit * lot, 2),
        "max_loss_inr": round(max_loss * lot, 2),
        "fair_net_premium": best["fair_credit"],
        "edge_vs_fair": best["edge_vs_fair"],
        "score": round(best["score"], 4),
        "reasoning": reasoning,
    }


def _call_debit(
    by_strike: dict[float, dict[str, Any]],
    *,
    atm: float,
    step: float,
    steps: int,
    min_ltp: float,
    lot: int,
) -> dict[str, Any] | None:
    """Buy cheaper CE / sell higher CE (1 step)."""
    candidates: list[dict[str, Any]] = []
    for k, row in by_strike.items():
        if not _in_window(k, atm, step, steps):
            continue
        ce = _leg(row, "ce")
        if not ce or ce["ltp"] < min_ltp:
            continue
        short_k = k + step
        if short_k not in by_strike:
            continue
        ce_short = _leg(by_strike[short_k], "ce")
        if not ce_short or ce_short["ltp"] < min_ltp:
            continue
        debit = round(ce["ltp"] - ce_short["ltp"], 4)
        if debit <= 0 or debit >= step:
            continue
        fair_debit = None
        if ce.get("bs_fair") is not None and ce_short.get("bs_fair") is not None:
            fair_debit = round(float(ce["bs_fair"]) - float(ce_short["bs_fair"]), 4)
        edge_vs_fair = (
            round(fair_debit - debit, 4) if fair_debit is not None else None
        )  # + means you pay less than fair
        # Prefer cheap long (negative edge)
        score = -ce["edge"] + max(0.0, ce_short["edge"] - ce["edge"])
        if edge_vs_fair is not None:
            score += max(0.0, edge_vs_fair)
        heston_ok = None
        if ce.get("heston_edge") is not None:
            heston_ok = ce["heston_edge"] < 0
        candidates.append(
            {
                "score": score,
                "long": ce,
                "short": ce_short,
                "debit": debit,
                "fair_debit": fair_debit,
                "edge_vs_fair": edge_vs_fair,
                "heston_agrees": heston_ok,
            }
        )
    if not candidates:
        return None
    best = max(candidates, key=lambda c: c["score"])
    width = float(step)
    debit = best["debit"]
    max_profit = round(width - debit, 4)
    max_loss = debit
    be = round(best["long"]["strike"] + debit, 4)
    heston_note = ""
    if best["heston_agrees"] is True:
        heston_note = " Heston also flags the long call as cheap."
    elif best["heston_agrees"] is False:
        heston_note = " Heston disagrees on cheapness — treat with caution."

    fair_txt = ""
    if best["fair_debit"] is not None and best["edge_vs_fair"] is not None:
        fair_txt = (
            f" BS-fair debit ≈ {best['fair_debit']:.2f}; you pay "
            f"{best['edge_vs_fair']:+.2f} pts vs fair (positive = cheaper entry)."
        )

    reasoning = (
        f"Buy CE {best['long']['strike']:.0f} (edge {best['long']['edge']:+.2f}, cheap) / "
        f"Sell CE {best['short']['strike']:.0f} (edge {best['short']['edge']:+.2f}). "
        f"Pay {debit:.2f} debit; max profit {max_profit:.2f}; breakeven {be:.2f}."
        f"{fair_txt}{heston_note}"
    )

    return {
        "id": "call_debit",
        "structure": "call_debit",
        "title": (
            f"Call debit — Buy CE {best['long']['strike']:.0f} / "
            f"Sell CE {best['short']['strike']:.0f}"
        ),
        "bias": "bullish",
        "action": "debit",
        "legs": [
            {
                "side": "buy",
                "option_type": "CE",
                "strike": best["long"]["strike"],
                "ltp": best["long"]["ltp"],
                "edge": best["long"]["edge"],
            },
            {
                "side": "sell",
                "option_type": "CE",
                "strike": best["short"]["strike"],
                "ltp": best["short"]["ltp"],
                "edge": best["short"]["edge"],
            },
        ],
        "net_premium": debit,
        "width": width,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "breakeven": be,
        "lot_size": lot,
        "net_premium_inr": round(debit * lot, 2),
        "max_profit_inr": round(max_profit * lot, 2),
        "max_loss_inr": round(max_loss * lot, 2),
        "fair_net_premium": best["fair_debit"],
        "edge_vs_fair": best["edge_vs_fair"],
        "score": round(best["score"], 4),
        "reasoning": reasoning,
    }


def build_recommendations(
    rows: list[dict[str, Any]],
    *,
    underlying: str,
    spot: float,
    atm_strike: float,
) -> list[dict[str, Any]]:
    """Ranked defined-risk ideas from desk rows."""
    meta = INDEX_OPTIONS.get(underlying) or {}
    step = float(meta.get("strike_step") or 50)
    lot = int(meta.get("lot_size") or 1)
    cfg = _cfg()
    steps = int(cfg.get("atm_window_steps", 2))
    min_ltp = float(cfg.get("min_ltp", 5.0))
    max_ideas = int(cfg.get("max_ideas", 3))
    disclaimer = str(
        cfg.get(
            "disclaimer",
            "Model suggestion — not advice; verify bid/ask before order. "
            "Does not arm or place orders.",
        )
    )

    by_strike = {float(r["strike"]): r for r in rows if r.get("strike") is not None}
    if not by_strike:
        return []

    strikes = sorted(by_strike.keys())
    if len(strikes) >= 2:
        gaps = [strikes[i + 1] - strikes[i] for i in range(len(strikes) - 1)]
        positive = [g for g in gaps if g > 0]
        if positive:
            step = float(min(positive))

    ideas: list[dict[str, Any]] = []
    for builder in (_bull_put_credit, _call_debit):
        idea = builder(
            by_strike,
            atm=float(atm_strike),
            step=step,
            steps=steps,
            min_ltp=min_ltp,
            lot=lot,
        )
        if idea:
            idea["spot"] = float(spot)
            idea["atm_strike"] = float(atm_strike)
            idea["underlying"] = underlying
            idea["disclaimer"] = disclaimer
            ideas.append(idea)

    ideas.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    return ideas[:max_ideas]

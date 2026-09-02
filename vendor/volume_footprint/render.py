"""Terminal rendering: the footprint table, the mobile dashboard, the profile.

The Pine original paints a chart table. Here the same numbers come out as text,
which is enough to read the tool and to diff two runs against each other. An
optional matplotlib plot lives in :func:`plot_profile` for the visual version.
"""

from __future__ import annotations

from .indicator import FootprintResult

__all__ = ["format_table", "format_dashboard", "format_profile", "plot_profile"]


def _compact(v: float | None) -> str:
    """One-decimal K/M/B, matching the Pine mobile dashboard formatting."""
    if v is None:
        return ""
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.1f}B"
    if a >= 1e6:
        return f"{v / 1e6:.1f}M"
    if a >= 1e3:
        return f"{v / 1e3:.1f}K"
    return f"{v:.1f}"


def format_table(res: FootprintResult, price_fmt: str = "{:.2f}") -> str:
    """The full footprint table: price ladder, per-bar cells, and a sum column.

    Each cell reads ``-sell +buy``. Column headers are bars back from the
    anchor: 0 is the newest bar, -1 the one before it. Headers can jump,
    because candles that never traded inside the frame take no column at all.

    Markers in the left margin:
        ``P``  point of control of the window
        ``V``  inside the value area
        ``^``  buy imbalance at this level, summed across the window
        ``v``  sell imbalance at this level
        ``>``  the axis row - where price is right now
    """
    n_rows = len(res.row_prices)
    cols = res.columns
    lines: list[str] = []

    head = f"{'':>4} {'PRICE':>12} "
    for col in cols:
        head += f"{('0' if col.offset == 0 else f'-{col.offset}'):>17} "
    head += f"{'SUM':>17}"
    lines.append(head)
    lines.append("-" * len(head))

    va = res.window_va
    for k in range(n_rows - 1, -1, -1):  # top of the ladder first
        marks = ""
        marks += "P" if k == res.window_poc_index else " "
        marks += "V" if va is not None and va.low_index <= k <= va.high_index else " "
        if res.window_buy_imbalance[k]:
            marks += "^"
        elif res.window_sell_imbalance[k]:
            marks += "v"
        else:
            marks += " "
        marks += ">" if res.row_prices[k] == res.axis_price else " "

        row = f"{marks:>4} {price_fmt.format(res.row_prices[k]):>12} "
        for col in cols:
            b = col.split.buy_rows[k]
            s = col.split.sell_rows[k]
            if b is None and s is None:
                cell = "."
            else:
                bi = "^" if col.split.buy_imbalance[k] else " "
                si = "v" if col.split.sell_imbalance[k] else " "
                cell = f"{si}-{_compact(s)} {bi}+{_compact(b)}"
            row += f"{cell:>17} "
        sum_s, sum_b = res.row_sum_sell[k], res.row_sum_buy[k]
        summary = "." if (sum_s <= 0.0 and sum_b <= 0.0) else f"-{_compact(sum_s)} +{_compact(sum_b)}"
        row += f"{summary:>17}"
        lines.append(row)

    lines.append("-" * len(head))
    tot = f"{'':>4} {'TOTAL':>12} "
    for col in cols:
        tot += f"{_compact(col.total):>17} "
    tot += f"{_compact(res.window_total_buy + res.window_total_sell):>17}"
    lines.append(tot)

    dlt = f"{'':>4} {'DELTA':>12} "
    for col in cols:
        dlt += f"{('+' if col.delta >= 0 else '') + _compact(col.delta):>17} "
    dlt += f"{('+' if res.window_delta >= 0 else '') + _compact(res.window_delta):>17}"
    lines.append(dlt)

    return "\n".join(lines)


def format_dashboard(res: FootprintResult, price_fmt: str = "{:.2f}") -> str:
    """The compact two-column readout: the numbers without the ladder."""
    p = res.profile
    last = res.columns[0].bar if res.columns else None
    rows: list[tuple[str, str]] = []

    if last is not None:
        rows.append(("Last price", price_fmt.format(last.close)))
        rows.append(("Bar volume", _compact(last.volume)))
        rows.append(
            (
                "Bar buy / sell",
                f"+{_compact(last.buy_volume)}  /  -{_compact(last.sell_volume)}",
            )
        )
        if last.buy_volume is not None and last.sell_volume is not None:
            d = last.buy_volume - last.sell_volume
            rows.append(("Bar delta", f"{'+' if d >= 0 else ''}{_compact(d)}"))

    rows.append(("", ""))
    rows.append(("Window bars", str(len(res.columns))))
    rows.append(
        (
            "Window buy / sell",
            f"+{_compact(res.window_total_buy)}  /  -{_compact(res.window_total_sell)}",
        )
    )
    rows.append(
        ("Window delta", f"{'+' if res.window_delta >= 0 else ''}{_compact(res.window_delta)}")
    )
    rows.append(
        (
            "Window POC",
            price_fmt.format(res.window_poc_price) if res.window_poc_price else "-",
        )
    )
    rows.append(
        (
            "Window VAH / VAL",
            f"{price_fmt.format(res.window_vah_price)} / {price_fmt.format(res.window_val_price)}"
            if res.window_vah_price is not None and res.window_val_price is not None
            else "-",
        )
    )

    rows.append(("", ""))
    rows.append(
        ("Chart POC", price_fmt.format(res.chart_poc_price) if res.chart_poc_price else "-")
    )
    if res.chart_poc_price is not None:
        rows.append(
            (
                "  POC buy / sell",
                f"+{_compact(res.chart_poc_buy)}  /  -{_compact(res.chart_poc_sell)}",
            )
        )
    rows.append(
        (
            "Chart VAH / VAL",
            f"{price_fmt.format(res.chart_vah_price)} / {price_fmt.format(res.chart_val_price)}"
            if res.chart_vah_price is not None and res.chart_val_price is not None
            else "-",
        )
    )
    if res.chart_imb_up is not None:
        side = "buy" if res.chart_imb_up[1] else "sell"
        rows.append(("Imbalance above", f"{price_fmt.format(res.chart_imb_up[0])} ({side})"))
    if res.chart_imb_down is not None:
        side = "buy" if res.chart_imb_down[1] else "sell"
        rows.append(("Imbalance below", f"{price_fmt.format(res.chart_imb_down[0])} ({side})"))

    if p is not None:
        rows.append(("", ""))
        rows.append(("OVL", f"{p.overlap:.2f} %" if p.overlap is not None else "-"))
        rows.append(("Tilt", f"{p.tilt:+.2f} pp" if p.tilt is not None else "-"))
        rows.append(("Balance", res.balance_verdict()))
        rows.append(
            (
                "RES",
                f"{p.residual_ppm:.4f} PPM  {res.residual_label}"
                if p.residual_ppm is not None
                else "-",
            )
        )

    width = max(len(k) for k, _ in rows) + 2
    return "\n".join(f"{k:<{width}}{v}" if k else "" for k, v in rows)


def format_profile(res: FootprintResult, width: int = 44, rows: int = 25) -> str:
    """ASCII rendering of the two bells, buy on the right, sell on the left.

    Both sides share one horizontal scale, so the relative size of the two
    bodies reflects real volume dominance rather than each being normalised to
    its own maximum.
    """
    if not res.profile_prices:
        return "(no profile data)"

    prices = res.profile_prices
    buys = res.profile_buy
    sells = res.profile_sell
    v_max = max(max(buys), max(sells)) or 1.0

    step = max(1, len(prices) // rows)
    out: list[str] = []
    for idx in range(len(prices) - 1, -1, -step):
        p = prices[idx]
        nb = int(round(width * buys[idx] / v_max))
        ns = int(round(width * sells[idx] / v_max))
        # Tolerance is half a *displayed* row, so a level that falls between
        # two printed samples still gets its marker instead of vanishing.
        tol = 0.5 * step * (prices[1] - prices[0]) if len(prices) > 1 else 0.0
        marker = " "
        if res.chart_poc_price is not None and abs(p - res.chart_poc_price) <= tol:
            marker = "P"
        elif res.chart_vah_price is not None and abs(p - res.chart_vah_price) <= tol:
            marker = "H"
        elif res.chart_val_price is not None and abs(p - res.chart_val_price) <= tol:
            marker = "L"
        out.append(f"{marker} {p:>12.2f} {'#' * ns:>{width}}|{'#' * nb:<{width}}")
    out.append(f"{'':>14} {'sell':>{width}}|{'buy':<{width}}")
    return "\n".join(out)


def plot_profile(res: FootprintResult, path: str | None = None, title: str = ""):
    """Matplotlib rendering of the profile. Requires matplotlib; optional."""
    try:
        import matplotlib

        if path:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("plot_profile needs matplotlib installed") from exc

    if not res.profile_prices:
        raise ValueError("no profile data to plot")

    prices = res.profile_prices
    fig, ax = plt.subplots(figsize=(7, 9))
    ax.fill_betweenx(prices, 0, res.profile_buy, alpha=0.35, color="#4FADFF", label="Buy")
    ax.fill_betweenx(prices, 0, res.profile_sell, alpha=0.35, color="#FF5C78", label="Sell")
    ax.plot(res.profile_buy, prices, color="#4FADFF", lw=1.2)
    ax.plot(res.profile_sell, prices, color="#FF5C78", lw=1.2)

    if res.chart_poc_price is not None:
        ax.axhline(res.chart_poc_price, color="#FF8A3D", lw=2, label="POC")
    for lvl, lbl in ((res.chart_vah_price, "VAH"), (res.chart_val_price, "VAL")):
        if lvl is not None:
            ax.axhline(lvl, color="#B39DFF", lw=1, ls="--", label=lbl)

    p = res.profile
    sub = ""
    if p is not None and p.overlap is not None:
        sub = f"OVL {p.overlap:.1f}%  ·  tilt {p.tilt:+.1f}pp  ·  {res.balance_verdict()}"
    ax.set_title(f"{title}\n{sub}".strip())
    ax.set_xlabel("volume per unit price")
    ax.set_ylabel("price")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()

    if path:
        fig.savefig(path, dpi=140)
        plt.close(fig)
        return path
    return fig

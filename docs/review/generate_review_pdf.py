"""Generate consolidated 3ST project review PDF."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fpdf import FPDF

OUT_DIR = Path(__file__).resolve().parent
PDF_PATH = OUT_DIR / "3ST_Project_Review_Gaps.pdf"


class ReviewPDF(FPDF):
    def header(self) -> None:
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(80, 80, 80)
        self.cell(0, 8, "3ST Kite Algo Platform - Implementation Review", align="R")
        self.ln(10)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def section_title(self, title: str, level: int = 1) -> None:
        self.set_x(self.l_margin)
        self.ln(4 if level == 1 else 2)
        size = 14 if level == 1 else 11
        self.set_font("Helvetica", "B", size)
        self.set_text_color(20, 60, 80)
        self.multi_cell(self.epw, 7, title)
        self.ln(2)

    def body_text(self, text: str) -> None:
        self.set_x(self.l_margin)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        self.multi_cell(self.epw, 5, text)
        self.ln(1)

    def bullet(self, text: str, indent: int = 0) -> None:
        self.set_x(self.l_margin + indent)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        self.multi_cell(self.epw - indent, 5, f"  -  {text}")

    def table_row(self, cols: list[str], widths: list[int], header: bool = False) -> None:
        self.set_x(self.l_margin)
        if header:
            self.set_font("Helvetica", "B", 9)
            self.set_fill_color(230, 240, 245)
        else:
            self.set_font("Helvetica", "", 9)
            self.set_fill_color(255, 255, 255)
        h = 7
        for col, w in zip(cols, widths):
            self.cell(w, h, col, border=1, fill=header)
        self.ln(h)
        self.set_x(self.l_margin)


def build_pdf() -> Path:
    pdf = ReviewPDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(15, 50, 70)
    pdf.cell(0, 12, "3ST Project Review", ln=True)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(0, 8, f"Consolidated gap analysis  |  {date.today().isoformat()}", ln=True)
    pdf.ln(4)
    pdf.body_text(
        "Stack: Python FastAPI backend + Pixel Perfect UI (React/Vite) + Kite Connect. "
        "Strategy: 3ST (Triple SuperTrend + ADX). This document consolidates review findings "
        "for configure / backtest / live workflow completeness."
    )

    # --- Working ---
    pdf.section_title("1. What Is Working")
    items_working = [
        "Stock Selection with strategy settings (ST method, ST1/ST2/ST3 toggles, ADX, SL/TGT/TSL, session mode)",
        "Settings persist via selection store and flow into backtest when use_selection=true",
        "Kite authentication and instrument search",
        "Backtest: Kite up to ~400 days intraday, date pickers, points PnL, long/short split, start open / end close",
        "Watchlist queue lifecycle: waiting -> triggered -> active -> closed",
        "Signal scan: 3ST entry detection on waiting watchlist items",
        "Options spread preview and save (legs stored; preview API working)",
    ]
    for item in items_working:
        pdf.bullet(item)

    # --- Critical ---
    pdf.section_title("2. Critical Gaps (Blocks Real Trading)")
    pdf.section_title("2.1 No Live Order Execution", level=2)
    pdf.body_text(
        "Broker adapters exist (broker/kite_broker.py, broker/paper_broker.py) but nothing calls "
        "place_order(). POST /watchlist/{id}/activate only updates status. Live Desk Activate "
        "does not place orders even when ARMED."
    )
    pdf.body_text("Flow today: Scan -> triggered -> [manual Activate] -> active -> NO orders.")

    pdf.section_title("2.2 Step 7 - Options Spread Execution Missing", level=2)
    pdf.body_text(
        "Spread legs are previewed and stored, but multi-leg order placement is not implemented. "
        "Backtest always runs on underlying OHLC, not spread PnL. watchlist_runner resolves index "
        "token for signals only; spread templates/legs ignored after trigger."
    )

    pdf.section_title("2.3 Live vs Backtest Logic Mismatch", level=2)
    pdf.ln(1)
    w = [55, 35, 35, 35]
    pdf.table_row(["Feature", "Backtest", "Live scan", "Streamlit"], w, header=True)
    rows = [
        ["Session / force exit", "Yes", "No", "Partial"],
        ["SL / TGT / TSL", "Yes", "No", "Partial"],
        ["Zone exits (ST1)", "Yes", "No", "Partial"],
        ["trade_mode Long/Short/Both", "Yes", "No", "Yes"],
        ["system_mode Intraday/Positional", "Yes", "No", "Partial"],
        ["ST enable flags", "Yes", "Entries only", "No"],
        ["Options spread product", "No", "Index signal only", "No"],
    ]
    for row in rows:
        pdf.table_row(row, w)
    pdf.ln(2)
    pdf.body_text(
        "Live scan uses strategies/three_st.py entry on last bar only. Backtest uses full "
        "backtest_engine with session, risk exits, and trade mode. Results can disagree materially."
    )

    pdf.section_title("2.4 Risk Limits API / UI Broken", level=2)
    pdf.table_row(["UI field (settings.tsx)", "API field (risk/limits.py)"], [90, 90], header=True)
    pdf.table_row(["max_loss_day", "max_daily_loss"], [90, 90])
    pdf.table_row(["max_trades_day", "(not defined)"], [90, 90])
    pdf.ln(2)
    pdf.body_text(
        "UI POSTs wrong field names; limits may not save. check_order() in risk/limits.py is never invoked."
    )

    # --- Important ---
    pdf.add_page()
    pdf.section_title("3. Important Gaps (Polish & Consistency)")

    important = [
        ("Hybrid ST method not implemented",
         "UI offers 'Hybrid (HA ST, regular close)' but strategy_3st.py treats hybrid same as heikin_ashi."),
        ("Streamlit app.py is stale",
         "Legacy Yahoo-only UI missing ST toggles, Kite source, new metrics, ATR TSL, date/Kite limits."),
        ("Session start/end not editable in UI",
         "session_start/session_end stored but only force_exit shown on Stock Selection page."),
        ("Scan errors hidden",
         "/watchlist/scan returns errors[] per item; Dashboard never displays them."),
        ("Options spread validation weak",
         "Can save queue items with empty legs/expiry; no server check that preview was run."),
        ("Auth UX gap",
         "API has /auth/login redirect; UI only supports manual request_token paste."),
        ("Port / env confusion",
         "UI api.ts defaults port 8000; .env uses 8001; easy misconfiguration."),
        ("No automated tests",
         "No project tests/ directory; strategy, API, spread builder untested."),
        ("Watchlist auto-activate not implemented",
         "Scan promotes waiting->triggered only; manual Activate required on Live Desk."),
        ("Paper broker LTP",
         "PaperBroker needs manual set_ltp(); positions stay empty without it."),
    ]
    for title, desc in important:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(30, 30, 30)
        pdf.multi_cell(pdf.epw, 5, title)
        pdf.body_text(desc)

    # --- Nice to have ---
    pdf.section_title("4. Nice-to-Have")
    nice = [
        "Backtest price/ST chart (API returns candles; UI shows equity/trades only)",
        "Server-side scan scheduler (browser polling only when tab open)",
        "trade_mode not persisted in selection (resets on Backtest page)",
        "BANKNIFTY spread: index_token_key is None in config.py",
        "iron_condor in UI with no 4-leg execution path",
        "Root README with run commands and port alignment",
        "CORS allows * with credentials (production concern)",
        "Duplicate watchlist entries not prevented",
        "Firstock client in requirements appears unused by main API",
    ]
    for item in nice:
        pdf.bullet(item)

    # --- Priority ---
    pdf.section_title("5. Recommended Priority")
    pdf.ln(1)
    w2 = [22, 108, 30]
    pdf.table_row(["Priority", "Task", "Effort"], w2, header=True)
    priorities = [
        ("P0", "Fix max_daily_loss / max_trades_day field mapping", "Small"),
        ("P0", "Align live scan with backtest engine (session, exits, SL/TGT/TSL)", "Medium"),
        ("P1", "Wire Activate + ARMED -> place_order() for underlying", "Medium"),
        ("P1", "Implement or remove hybrid ST method", "Small"),
        ("P1", "Show scan errors on Dashboard", "Small"),
        ("P2", "Multi-leg spread orders (Step 7)", "Large"),
        ("P2", "Options-spread backtest path", "Large"),
        ("P3", "Update or deprecate Streamlit app.py", "Medium"),
        ("P3", "README + port standardization", "Small"),
    ]
    for p, task, effort in priorities:
        pdf.table_row([p, task, effort], w2)

    pdf.ln(4)
    pdf.section_title("6. Summary")
    pdf.body_text(
        "The research / configure / backtest loop is solid. The execute loop is not: "
        "scan -> trigger -> activate does not place orders, risk settings may not apply, "
        "and live signal logic is simpler than backtest."
    )
    pdf.body_text(
        "Highest-impact path to live trading: (1) fix risk limits naming, "
        "(2) make live scanning use same rules as backtest, "
        "(3) wire Activate + ARMED to place_order()."
    )

    pdf.ln(6)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.multi_cell(
        pdf.epw,
        5,
        "Generated from codebase review of api/main.py, backtest_engine.py, strategy_3st.py, "
        "execution/, Pixel Perfect UI/src/, app.py, watchlist_store.py, selection_store.py, risk/limits.py.",
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pdf.output(str(PDF_PATH))
    return PDF_PATH


if __name__ == "__main__":
    path = build_pdf()
    print(f"Wrote {path}")

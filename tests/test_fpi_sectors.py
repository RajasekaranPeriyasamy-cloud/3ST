"""Tests for NSDL FPI sector parser and RRG overlay."""

from __future__ import annotations

import json
from pathlib import Path

from analysis.fpi_sectors import (
    attach_fpi_overlay,
    fpi_confluence,
    fpi_status,
    parse_fpi_report_html,
)


def _sample_row(sector: str, sr: int, ni1: str, ni2: str) -> str:
    cells = [""] * 98
    cells[0] = str(sr)
    cells[1] = sector
    cells[26] = ni1
    cells[50] = ni2
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def test_parse_fpi_html_fixture() -> None:
    html = f"""
    <html><body><table>
    <tr><td colspan="40">AUC as on June 30, 2026</td></tr>
    <tr><td colspan="20">Net Investment June 01-15, 2026</td>
        <td colspan="20">Net Investment June 16-30, 2026</td></tr>
    <tr><td>Sr. No.</td><td>Sectors</td><td>Equity</td></tr>
    {_sample_row("Information Technology", 13, "-6,733", "-733")}
    {_sample_row("Financial Services", 10, "-11,263", "14,634")}
    <tr><td></td><td>Grand Total</td><td>0</td></tr>
    </table></body></html>
    """
    parsed = parse_fpi_report_html(html, source_url="test://fixture")
    assert "Information Technology" in parsed["sectors"]
    it = parsed["sectors"]["Information Technology"]["net_equity_inr"]
    assert it["period1"] == -6733.0
    assert it["period2"] == -733.0
    assert it["month_total"] == -7466.0


def test_fpi_confluence_rules() -> None:
    assert fpi_confluence("leading", 100.0) == "aligned"
    assert fpi_confluence("leading", -50.0) == "divergence"
    assert fpi_confluence("lagging", -10.0) == "aligned"
    assert fpi_confluence("lagging", 10.0) == "contrarian"
    assert fpi_confluence("improving", 5.0) == "aligned"


def test_attach_fpi_overlay_uses_seed(monkeypatch) -> None:
    seed_path = Path(__file__).resolve().parents[1] / "data" / "fpi_sectors_seed.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))

    def _fake_load(*, force_refresh: bool = False):
        return seed

    monkeypatch.setattr("analysis.fpi_sectors.load_fpi_sectors", _fake_load)
    snapshot = {
        "symbols": [
            {"symbol": "NIFTY_IT", "label": "Nifty IT", "quadrant": "lagging"},
            {"symbol": "NIFTY_BANK", "label": "Nifty Bank", "quadrant": "improving"},
            {"symbol": "RELIANCE", "label": "Reliance", "quadrant": "leading"},
        ]
    }
    out = attach_fpi_overlay(snapshot, period="period2")
    assert out["fpi"]["ok"] is True
    it_row = next(s for s in out["symbols"] if s["symbol"] == "NIFTY_IT")
    assert it_row["fpi"]["net_equity_inr"] == -733.0
    assert it_row["fpi"]["confluence"] == "aligned"
    bank = next(s for s in out["symbols"] if s["symbol"] == "NIFTY_BANK")
    assert bank["fpi"]["net_equity_inr"] == 14634.0
    assert bank["fpi"]["alias_of"] == "NIFTY_FIN_SERVICE"
    rel = next(s for s in out["symbols"] if s["symbol"] == "RELIANCE")
    assert rel["fpi"] is None


def test_fpi_status_from_seed(monkeypatch) -> None:
    seed_path = Path(__file__).resolve().parents[1] / "data" / "fpi_sectors_seed.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))

    monkeypatch.setattr("analysis.fpi_sectors.load_fpi_sectors", lambda **_: seed)
    status = fpi_status()
    assert status["ok"] is True
    assert status["mapped_rrg_sectors"] >= 10

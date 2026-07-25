"""
NSDL fortnightly sector-wise FPI / FII equity investment — parse, cache, RRG overlay.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal

import requests

from config import FPI_DEFAULTS, FPI_RRG_ALIASES, FPI_SECTOR_TO_RRG, RRG_SECTOR_INDICES
from settings import data_dir

Confluence = Literal["aligned", "divergence", "watch", "contrarian", "neutral", "n/a"]

CACHE_FILE = data_dir() / "fpi_sectors.json"
SEED_FILE = Path(__file__).resolve().parent.parent / "data" / "fpi_sectors_seed.json"

# NSDL fortnightly table (98 cells per sector row): Sr.No + Sector + AUC blocks + NI blocks.
# Net investment equity INR: period1 at index 26, period2 at index 50 (0-based).
_NI1_EQUITY_IDX = 26
_NI2_EQUITY_IDX = 50


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_tr = False
        self._in_td = False
        self._cell = ""
        self._row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._in_tr = True
            self._row = []
        elif tag == "td" and self._in_tr:
            self._in_td = True
            self._cell = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_td:
            self._in_td = False
            self._row.append(re.sub(r"\s+", " ", self._cell).strip())
        elif tag == "tr" and self._in_tr:
            self._in_tr = False
            if self._row:
                self.rows.append(self._row)

    def handle_data(self, data: str) -> None:
        if self._in_td:
            self._cell += data


def _clean_num(text: str) -> float | None:
    token = (text or "").strip().replace(",", "").replace(" ", "")
    if not token or token == "-":
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _extract_period_labels(rows: list[list[str]]) -> tuple[str | None, str | None]:
    p1: str | None = None
    p2: str | None = None
    for row in rows[:8]:
        for cell in row:
            if "Net Investment" not in cell:
                continue
            label = re.sub(r"\s+", " ", cell.strip())
            if p1 is None:
                p1 = label
            elif p2 is None and label != p1:
                p2 = label
    return p1, p2


def parse_fpi_report_html(html: str, *, source_url: str = "") -> dict[str, Any]:
    parser = _TableParser()
    parser.feed(html)
    rows = [r for r in parser.rows if any(c.strip() for c in r)]
    period1_label, period2_label = _extract_period_labels(rows)

    sectors: dict[str, dict[str, Any]] = {}
    as_of: str | None = None
    for row in rows:
        if len(row) <= _NI2_EQUITY_IDX:
            continue
        sr = row[0].strip()
        name = row[1].strip()
        if not sr.isdigit() or not name:
            continue
        if name.lower() in {"sectors", "grand total"}:
            if name.lower() == "grand total":
                break
            continue

        ni1 = _clean_num(row[_NI1_EQUITY_IDX])
        ni2 = _clean_num(row[_NI2_EQUITY_IDX])
        month_total: float | None = None
        if ni1 is not None and ni2 is not None:
            month_total = ni1 + ni2

        sectors[name] = {
            "fpi_sector": name,
            "net_equity_inr": {
                "period1": ni1,
                "period2": ni2,
                "month_total": month_total,
            },
            "rrg_sector_id": FPI_SECTOR_TO_RRG.get(name),
        }

    for row in rows[:8]:
        for cell in row:
            m = re.search(r"as on\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", cell, re.I)
            if m and not as_of:
                as_of = m.group(1)

    fetched_at = datetime.now().replace(microsecond=0).isoformat()
    return {
        "ok": True,
        "source_url": source_url,
        "as_of": as_of,
        "period1_label": period1_label,
        "period2_label": period2_label,
        "fetched_at": fetched_at,
        "sectors": sectors,
    }


def fpi_confluence(quadrant: str, net_equity_inr: float | None) -> Confluence:
    if net_equity_inr is None:
        return "n/a"
    inflow = net_equity_inr > 0
    outflow = net_equity_inr < 0
    q = quadrant.lower()
    if q == "leading":
        if inflow:
            return "aligned"
        if outflow:
            return "divergence"
    elif q == "improving":
        if inflow:
            return "aligned"
        if outflow:
            return "watch"
    elif q == "weakening":
        if outflow:
            return "aligned"
        if inflow:
            return "watch"
    elif q == "lagging":
        if outflow:
            return "aligned"
        if inflow:
            return "contrarian"
    return "neutral"


def _flow_label(net: float | None) -> str:
    if net is None:
        return "—"
    if net > 0:
        return "Inflow"
    if net < 0:
        return "Outflow"
    return "Flat"


def _cache_fresh(path: Path, max_age_hours: int) -> bool:
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return datetime.now() - mtime < timedelta(hours=max_age_hours)


def _fetch_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = requests.get(url, headers=headers, timeout=45)
    resp.raise_for_status()
    return resp.text


def load_fpi_sectors(*, force_refresh: bool = False) -> dict[str, Any]:
    max_age = int(FPI_DEFAULTS.get("cache_hours", 24))
    url = str(FPI_DEFAULTS.get("report_url") or "")

    if not force_refresh and _cache_fresh(CACHE_FILE, max_age):
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))

    errors: list[str] = []
    if url:
        try:
            html = _fetch_html(url)
            parsed = parse_fpi_report_html(html, source_url=url)
            if parsed.get("sectors"):
                CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                CACHE_FILE.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
                return parsed
            errors.append("Parsed report contained no sectors")
        except Exception as exc:
            errors.append(str(exc))

    if CACHE_FILE.exists() and not force_refresh:
        cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        cached["stale"] = True
        if errors:
            cached["fetch_errors"] = errors
        return cached

    if SEED_FILE.exists():
        seed = json.loads(SEED_FILE.read_text(encoding="utf-8"))
        seed["stale"] = True
        seed["fetch_errors"] = errors or ["Using bundled seed data"]
        return seed

    raise RuntimeError(
        "FPI data unavailable. "
        + ("; ".join(errors) if errors else "Configure FPI report URL and retry.")
    )


def fpi_by_rrg_sector_id(fpi_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for _name, row in (fpi_data.get("sectors") or {}).items():
        sid = row.get("rrg_sector_id")
        if sid:
            out[str(sid)] = row
    return out


def _resolve_fpi_for_rrg(
    by_rrg: dict[str, dict[str, Any]], rrg_symbol: str
) -> tuple[dict[str, Any] | None, str | None]:
    direct = by_rrg.get(rrg_symbol)
    if direct:
        return direct, None
    alias = FPI_RRG_ALIASES.get(rrg_symbol)
    if alias and alias in by_rrg:
        return by_rrg[alias], alias
    return None, None


def attach_fpi_overlay(
    snapshot: dict[str, Any],
    *,
    period: str = "period2",
    fpi_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge NSDL FPI net equity into RRG snapshot symbol rows."""
    try:
        fpi = fpi_data if fpi_data is not None else load_fpi_sectors()
    except Exception as exc:
        snapshot["fpi"] = {"ok": False, "error": str(exc)}
        return snapshot

    by_rrg = fpi_by_rrg_sector_id(fpi)
    period_key = period if period in {"period1", "period2", "month_total"} else "period2"

    for sym in snapshot.get("symbols") or []:
        sid = str(sym.get("symbol") or "")
        fpi_row, alias_of = _resolve_fpi_for_rrg(by_rrg, sid)
        if not fpi_row:
            sym["fpi"] = None
            continue
        net_map = fpi_row.get("net_equity_inr") or {}
        net = net_map.get(period_key)
        label = str(fpi_row.get("fpi_sector") or "")
        if alias_of:
            proxy = RRG_SECTOR_INDICES.get(alias_of, {}).get("label") or alias_of
            label = f"{label} (via {proxy})"
        sym["fpi"] = {
            "fpi_sector": label,
            "net_equity_inr": net,
            "flow": _flow_label(net if isinstance(net, (int, float)) else None),
            "confluence": fpi_confluence(str(sym.get("quadrant") or ""), net),
            "period": period_key,
            "alias_of": alias_of,
        }

    snapshot["fpi"] = {
        "ok": True,
        "as_of": fpi.get("as_of"),
        "period1_label": fpi.get("period1_label"),
        "period2_label": fpi.get("period2_label"),
        "fetched_at": fpi.get("fetched_at"),
        "source_url": fpi.get("source_url"),
        "stale": bool(fpi.get("stale")),
        "period": period_key,
        "mapped_sectors": len(by_rrg),
    }
    return snapshot


def fpi_status() -> dict[str, Any]:
    try:
        data = load_fpi_sectors()
        by_rrg = fpi_by_rrg_sector_id(data)
        return {
            "ok": True,
            "cached": CACHE_FILE.exists(),
            "cache_file": str(CACHE_FILE),
            "as_of": data.get("as_of"),
            "period1_label": data.get("period1_label"),
            "period2_label": data.get("period2_label"),
            "fetched_at": data.get("fetched_at"),
            "source_url": data.get("source_url"),
            "stale": bool(data.get("stale")),
            "sector_count": len(data.get("sectors") or {}),
            "mapped_rrg_sectors": len(by_rrg),
            "rrg_mappings": [
                {
                    "fpi_sector": name,
                    "rrg_sector_id": cfg,
                    "rrg_label": RRG_SECTOR_INDICES.get(str(cfg), {}).get("label"),
                }
                for name, cfg in FPI_SECTOR_TO_RRG.items()
            ],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

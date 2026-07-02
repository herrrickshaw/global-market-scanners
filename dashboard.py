#!/usr/bin/env python3
"""
dashboard.py
------------
Results dashboard (SAFe F8.2): a single self-contained HTML page that renders the
key warehouse views — market coverage, the DVM classification distribution, the
top GGG Strong Performers, high-quality value names, and the current
accumulation/CMF screen — so results are viewable without running anything.
`render_html` is pure (takes DataFrames, returns an HTML string) and unit-tested;
the CLI queries the DuckDB warehouse and writes dashboard.html.

Usage:
  python dashboard.py                       # -> dashboard.html
  python dashboard.py --open                # build and open in browser
"""

from __future__ import annotations

import argparse
import datetime as dt
import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "dashboard.html")

_CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:2rem;color:#1a1a1a;background:#fafafa}
h1{font-size:1.5rem}h2{font-size:1.1rem;margin-top:2rem;border-bottom:2px solid #e0e0e0;padding-bottom:.3rem}
table{border-collapse:collapse;width:100%;margin:.5rem 0;background:#fff;font-size:.85rem}
th,td{border:1px solid #e5e5e5;padding:.35rem .6rem;text-align:right}
th{background:#f0f3f7;text-align:left}td:first-child,th:first-child{text-align:left}
tr:nth-child(even){background:#fbfcfd}.meta{color:#888;font-size:.8rem}
.badge{display:inline-block;background:#2b6cb0;color:#fff;border-radius:3px;padding:.1rem .4rem;font-size:.75rem}
"""


def render_html(sections: dict, generated: str | None = None) -> str:
    """sections: {title: DataFrame}. Returns a full HTML document string."""
    generated = generated or dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [f"<!doctype html><html><head><meta charset='utf-8'>",
             f"<title>Global Market Scanners</title><style>{_CSS}</style></head><body>",
             f"<h1>Global Market Scanners <span class='badge'>dashboard</span></h1>",
             f"<p class='meta'>generated {generated}</p>"]
    for title, df in sections.items():
        parts.append(f"<h2>{title}</h2>")
        if df is None or len(df) == 0:
            parts.append("<p class='meta'>no data</p>")
        else:
            parts.append(df.to_html(index=False, border=0))
    parts.append("</body></html>")
    return "".join(parts)


def _accumulation_section(markets=None, top: int = 15) -> pd.DataFrame:
    """Current top names by the accumulation/CMF signal (accumulation_screener) — the
    validated multi-month accumulation screen, for the daily dashboard."""
    import accumulation_screener as acc
    if markets is None:
        markets = [f.split("cleaned_long_")[1].split(".")[0]
                   for f in sorted(os.listdir(acc.SEED)) if f.startswith("cleaned_long_")]
    frames = [acc.current_screen(m) for m in markets]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True).dropna(subset=["accum"])
    df = df.sort_values("accum", ascending=False).head(top)
    df["cmf"] = df["cmf"].round(2); df["accum"] = df["accum"].round(2)
    return df[["market", "ticker", "close", "cmf", "accum"]]


def _query_all(accum_top: int = 15, include_accum: bool = True) -> dict:
    import duckdb
    import warehouse
    con = duckdb.connect(os.path.join(HERE, "market.duckdb"))
    warehouse.build(con)
    con.execute("SET max_expression_depth=10000")
    sec = {}
    try:
        sec["Market coverage"] = con.execute(warehouse.SHOWS["markets"]).df()
        sec["DVM classification"] = con.execute(warehouse.SHOWS["dvm_dist"]).df()
        sec["Top GGG Strong Performers"] = con.execute(warehouse.SHOWS["ggg_global"]).df()
        sec["High ROE / low D-E"] = con.execute(warehouse.SHOWS["high_roe_low_de"]).df()
    finally:
        con.close()
    if include_accum:
        # isolated so a screener hiccup never breaks the rest of the dashboard
        try:
            sec["Accumulation / CMF screen (top by accumulation, validated 6m signal)"] = \
                _accumulation_section(top=accum_top)
        except Exception as e:                     # noqa: BLE001
            print(f"  [dashboard] accumulation section skipped: {e}")
            sec["Accumulation / CMF screen (top by accumulation, validated 6m signal)"] = pd.DataFrame()
    return sec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--accum-top", type=int, default=15, help="rows in the accumulation section")
    ap.add_argument("--no-accum", action="store_true", help="skip the accumulation screen section")
    args = ap.parse_args()

    html = render_html(_query_all(accum_top=args.accum_top, include_accum=not args.no_accum))
    open(args.out, "w").write(html)
    print(f"wrote {args.out} ({len(html)//1024} KB)")
    if args.open:
        import webbrowser
        webbrowser.open(f"file://{os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()

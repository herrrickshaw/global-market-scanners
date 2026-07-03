#!/usr/bin/env python3
"""
brazil_cvm.py  —  Remediation L1/L2/L3 for Brazil: a filed-date fundamentals
fetcher built on the CVM open-data portal (dados.cvm.gov.br).

CVM (Comissão de Valores Mobiliários) publishes every listed company's
standardised financial statements as yearly ZIPs of semicolon-delimited,
ISO-8859-1 CSVs:

  DFP (annual):    https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_YYYY.zip
  ITR (quarterly): https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/itr_cia_aberta_YYYY.zip

Each ZIP holds a HEADER csv — `{doc}_cia_aberta_YYYY.csv` — whose **DT_RECEB**
column is the date CVM received the filing (the point-in-time timestamp), plus
per-statement CSVs (DRE income, BPA assets, BPP liabilities+equity). We join them
on (CD_CVM, DT_REFER, VERSAO) and emit a filed-date panel that plugs straight into
`pit_panel.as_of()` — no look-ahead.

Design:
  - PARSE functions (parse_header, parse_statement, build_panel) are pure and take
    raw CSV bytes/str, so they unit-test with tiny in-memory fixtures — no network.
  - FETCH functions (fetch_zip, filed_panel) hit CVM and cache the ZIP to disk.

CLI:  python brazil_cvm.py 2023            # download DFP 2023, print a panel sample

Caveat — financial sector: banks and insurers file under CVM's sector-specific
chart of accounts, so the standard equity/income codes in ACCOUNTS map to the
wrong lines for them (their ROE will look implausible, e.g. >100%). The mapping
below is correct for the general (industrial/commercial) taxonomy; a
sector-specific ACCOUNTS variant is the follow-up. `plausible_roe` flags the bad
ones so they can be excluded until then.

Refs: dados.cvm.gov.br. Not investment advice.
"""
from __future__ import annotations

import csv
import io
import os
import re
import unicodedata
import zipfile
from datetime import date, datetime

BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/{doc}/DADOS/{pfx}_cia_aberta_{year}.zip"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cvm_cache")
ENC = "latin-1"                       # CVM files are ISO-8859-1

# CVM standard account codes (CD_CONTA) -> our metric name, per statement.
# Consolidated ("con") preferred; we fall back to individual ("ind").
# Income (3.11) and assets (1) share codes across sectors. EQUITY is the exception:
# industrials carry Patrimônio Líquido at 2.03, but banks/insurers (financial
# taxonomy) put it at 2.07 — so equity is matched by DESCRIPTION (parse_equity),
# not a fixed code, which handles both.
ACCOUNTS = {
    "DRE": {"3.01": "revenue", "3.11": "net_income"},   # Receita; Lucro/Prejuízo do Período
    "BPA": {"1": "assets"},                              # Ativo Total
}
_EQUITY_DEPTH2 = re.compile(r"^2\.\d+$")                 # top-level passivo line, e.g. 2.03 / 2.07


def _norm(s: str) -> str:
    """Lowercase, accent-stripped — so 'Patrimônio Líquido' -> 'patrimonio liquido'."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def _d(s):
    s = str(s)[:10]
    return datetime.strptime(s, "%Y-%m-%d").date() if s else None


def _num(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s.replace(",", "."))     # CVM uses no thousands sep
    except ValueError:
        return None


def _scale(escala_moeda: str) -> float:
    return 1000.0 if (escala_moeda or "").strip().upper() == "MIL" else 1.0


def plausible_roe(roe) -> bool:
    """True if ROE is in a believable band (−100%..+100%). Financial-sector filings
    under the wrong taxonomy produce out-of-band values; use this to exclude them
    until sector-specific account codes are added."""
    return roe is not None and -1.0 <= roe <= 1.0


def _reader(data):
    """Yield dict rows from CVM CSV bytes/str (';'-delimited, latin-1)."""
    text = data.decode(ENC) if isinstance(data, (bytes, bytearray)) else data
    return csv.DictReader(io.StringIO(text), delimiter=";")


# ── pure parsers ──────────────────────────────────────────────────────────────
def parse_header(data) -> dict:
    """Map (CD_CVM, DT_REFER, VERSAO) -> filing metadata incl. filed_date (DT_RECEB)."""
    out = {}
    for r in _reader(data):
        key = (r["CD_CVM"].strip(), r["DT_REFER"].strip(), r["VERSAO"].strip())
        out[key] = {
            "filed_date": r.get("DT_RECEB", "").strip(),
            "name": r.get("DENOM_CIA", "").strip(),
            "cnpj": r.get("CNPJ_CIA", "").strip(),
            "categ": r.get("CATEG_DOC", "").strip(),
        }
    return out


def parse_statement(data, wanted: dict) -> dict:
    """Map (CD_CVM, DT_REFER, VERSAO) -> {metric: scaled value} for the CURRENT period.

    `wanted`: {CD_CONTA: metric_name}. Keeps only the reporting period
    (DT_FIM_EXERC == DT_REFER), scaling by ESCALA_MOEDA (MIL -> x1000).
    """
    out: dict = {}
    for r in _reader(data):
        if r.get("DT_FIM_EXERC", "").strip() != r.get("DT_REFER", "").strip():
            continue                                   # not the reference period
        code = r.get("CD_CONTA", "").strip()
        if code not in wanted:
            continue
        key = (r["CD_CVM"].strip(), r["DT_REFER"].strip(), r["VERSAO"].strip())
        val = _num(r.get("VL_CONTA"))
        if val is None:
            continue
        out.setdefault(key, {})[wanted[code]] = val * _scale(r.get("ESCALA_MOEDA", ""))
    return out


def parse_equity(data) -> dict:
    """Equity per (CD_CVM, DT_REFER, VERSAO): the top-level (depth-2) BPP account
    whose description is 'Patrimônio Líquido…'. This matches 2.03 for industrials
    AND 2.07 for the financial sector, fixing the bank ROE bug without needing to
    know a company's sector in advance."""
    out: dict = {}
    for r in _reader(data):
        if r.get("DT_FIM_EXERC", "").strip() != r.get("DT_REFER", "").strip():
            continue
        if not _EQUITY_DEPTH2.match(r.get("CD_CONTA", "").strip()):
            continue
        if not _norm(r.get("DS_CONTA", "")).startswith("patrimonio liquido"):
            continue
        val = _num(r.get("VL_CONTA"))
        if val is None:
            continue
        key = (r["CD_CVM"].strip(), r["DT_REFER"].strip(), r["VERSAO"].strip())
        out[key] = {"equity": val * _scale(r.get("ESCALA_MOEDA", ""))}
    return out


def build_panel(header: dict, statements: list) -> list:
    """Merge header + statement dicts into a filed-date panel.

    Emits ONE record per (company, fiscal period, VERSAO) — every filing/restatement
    is its own dated observation, NOT collapsed to the latest version. That is what
    keeps it point-in-time: `pit_panel.as_of(panel, T)` then picks, per company, the
    row with the greatest filed_date that is <= T (so an early date sees v1 and a
    later date sees the restated v2). Records:
      {cd_cvm, name, filed_date, fiscal_end, version, revenue, net_income,
       assets, equity, roe}.
    """
    out = []
    for key, h in header.items():
        cd, ref, ver = key
        rec = {"cd_cvm": cd, "name": h.get("name", ""), "filed_date": h.get("filed_date", ""),
               "fiscal_end": ref, "version": ver,
               "revenue": None, "net_income": None, "assets": None, "equity": None}
        for st in statements:
            for metric, val in st.get(key, {}).items():
                rec[metric] = val
        ni, eq = rec["net_income"], rec["equity"]
        rec["roe"] = (ni / eq) if (ni is not None and eq not in (None, 0)) else None
        if rec["filed_date"] and any(rec[m] is not None
                                     for m in ("revenue", "net_income", "assets", "equity")):
            out.append(rec)
    return out


# ── networked fetch ───────────────────────────────────────────────────────────
def fetch_zip(year: int, doc: str = "DFP", cache_dir: str = CACHE_DIR) -> dict:
    """Download (and cache) a CVM yearly ZIP; return {member_name: bytes}."""
    pfx = doc.lower()
    url = BASE.format(doc=doc.upper(), pfx=pfx, year=year)
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{pfx}_cia_aberta_{year}.zip")
    if not os.path.exists(path):
        try:
            from apiclient import http_get                       # governed fetch if available
            r = http_get("cvm", url, headers={"User-Agent": "market-research admin@gms.dev"})
            blob = r.content
        except Exception:
            import requests
            blob = requests.get(url, headers={"User-Agent": "market-research admin@gms.dev"},
                                timeout=120).content
        with open(path, "wb") as fh:
            fh.write(blob)
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def filed_panel(year: int, doc: str = "DFP", prefer: str = "con",
                cache_dir: str = CACHE_DIR) -> list:
    """End-to-end: fetch a CVM year and return its point-in-time fundamentals panel."""
    pfx = doc.lower()
    members = fetch_zip(year, doc, cache_dir)
    header = parse_header(members[f"{pfx}_cia_aberta_{year}.csv"])
    statements = []
    for stmt, wanted in ACCOUNTS.items():                       # DRE, BPA (code-based)
        data = (members.get(f"{pfx}_cia_aberta_{stmt}_{prefer}_{year}.csv")
                or members.get(f"{pfx}_cia_aberta_{stmt}_ind_{year}.csv"))
        if data is not None:
            statements.append(parse_statement(data, wanted))
    bpp = (members.get(f"{pfx}_cia_aberta_BPP_{prefer}_{year}.csv")             # equity by desc
           or members.get(f"{pfx}_cia_aberta_BPP_ind_{year}.csv"))
    if bpp is not None:
        statements.append(parse_equity(bpp))
    return build_panel(header, statements)


def main():
    import sys
    year = int(sys.argv[1]) if len(sys.argv) > 1 else datetime.now().year - 1
    doc = sys.argv[2].upper() if len(sys.argv) > 2 else "DFP"
    print(f"brazil_cvm.py — CVM {doc} filed-date panel for {year}\n")
    try:
        panel = filed_panel(year, doc)
    except Exception as e:
        print(f"(fetch failed: {e})"); return
    print(f"companies with a dated filing: {len(panel)}\n")
    for rec in sorted(panel, key=lambda r: r.get("assets") or 0, reverse=True)[:8]:
        roe = f"{rec['roe']*100:5.1f}%" if rec["roe"] is not None else "  n/a"
        print(f"  CVM {rec['cd_cvm']:>7s} filed {rec['filed_date']} (FY {rec['fiscal_end']}) "
              f"ROE {roe}  {rec['name'][:34]}")
    # demonstrate the point-in-time guarantee
    try:
        import pit_panel as pit
        asof = f"{year+1}-06-30"
        pit_view = pit.as_of(panel, asof, symbol_col="cd_cvm", filed_col="filed_date")
        print(f"\npit_panel.as_of({asof}) -> {len(pit_view)} companies known by then "
              f"(look-ahead leak: {pit.leaks_lookahead(panel, asof, filed_col='filed_date')})")
    except Exception:
        pass


if __name__ == "__main__":
    main()

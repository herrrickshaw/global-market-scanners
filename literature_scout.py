#!/usr/bin/env python3
"""
literature_scout.py
-------------------
A literature scout for global equity-market research. It is *seeded* from the
sample papers this platform already implemented — Markowitz (1952), Sharpe (1964),
Fama (1991), Fama-French (1992), Piotroski (2000), Jegadeesh-Titman (1993),
Novy-Marx (2013), Frazzini-Pedersen (2014), Asness-Frazzini-Pedersen QMJ (2019),
Gu-Kelly-Xiu ML (2020), and Jacob-Pradeep-Varma (IIMA 2022) — and searches open
scholarly APIs (OpenAlex, arXiv, Crossref) for related and *new* work, scores each
hit against the platform's themes, maps it to the module that implements (or would
implement) it, and flags **research gaps** — frontier themes the platform doesn't
cover yet.

Design:
  * pure scoring / mapping / dedup / ranking core  -> unit-tested, offline
  * network fetch via the governed apiclient (polite pools, adaptive backoff)
  * a built-in SEED_PAPERS corpus (the scouted samples) so it produces a useful
    report with no network at all (`--offline`), and so CI/tests are deterministic

Usage:
  python literature_scout.py                          # scout the seed themes (network)
  python literature_scout.py --query "quality factor emerging markets" --limit 25
  python literature_scout.py --offline                # score the built-in corpus only
  python literature_scout.py --gaps                   # only the research-gap opportunities
  python literature_scout.py --out LITERATURE_SCOUT.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))

# ── themes the platform ALREADY implements (paper -> keywords -> module) ───────
COVERED_THEMES = {
    "quality":        {"kw": ["quality factor", "quality minus junk", "qmj", "profitability factor",
                              "piotroski", "gross profitability", "earnings quality"],
                       "modules": ["quality_factor.py", "dvm_composite.py"]},
    "value":          {"kw": ["value factor", "book-to-market", "hml", "value premium", "cheapness"],
                       "modules": ["factor_research.py", "dvm_composite.py"]},
    "momentum":       {"kw": ["momentum", "cross-sectional momentum", "time-series momentum",
                              "trend following", "52-week high"],
                       "modules": ["dvm_global.py", "ml_signal_engine.py", "sector_rotation.py"]},
    "low_risk":       {"kw": ["low volatility", "betting against beta", "low beta",
                              "idiosyncratic volatility", "minimum variance"],
                       "modules": ["risk.py", "portfolio.py", "quality_factor.py"]},
    "size":           {"kw": ["size factor", "small cap", "smb"],
                       "modules": ["factor_research.py"]},
    "mean_variance":  {"kw": ["mean-variance", "markowitz", "portfolio optimization",
                              "efficient frontier", "maximum sharpe", "tangency portfolio"],
                       "modules": ["portfolio.py", "factor_research.py"]},
    "capm":           {"kw": ["capm", "capital asset pricing", "systematic risk", "beta pricing"],
                       "modules": ["factor_research.py"]},
    "ml_pricing":     {"kw": ["machine learning", "deep learning", "cross-section of returns",
                              "empirical asset pricing", "neural network returns", "random forest returns"],
                       "modules": ["ml_screen_discovery.py", "ml_viability.py", "ml_signal_engine.py"]},
    "pit_bias":       {"kw": ["look-ahead bias", "point-in-time", "survivorship bias",
                              "data snooping", "backtest overfitting"],
                       "modules": ["pit_fundamentals.py", "pit_backtest.py", "pit_global.py"]},
    "costs":          {"kw": ["transaction cost", "implementation shortfall", "portfolio turnover",
                              "trading cost", "market impact", "capacity of a strategy"],
                       "modules": ["apply_costs.py", "portfolio.py"]},
    "emerging":       {"kw": ["emerging market", "india equity", "china equity", "frontier market",
                              "tunnelling", "corporate governance"],
                       "modules": ["full_indian_market_scan.py", "quality_factor.py"]},
    "multifactor":    {"kw": ["five-factor", "q-factor", "factor zoo", "replicating anomalies",
                              "which factors", "multifactor model"],
                       "modules": ["factor_research.py", "meta_screen.py"]},
    # closed by the scout->implement loop: PEAD was flagged as a gap, now implemented.
    "pead_revisions": {"kw": ["post-earnings-announcement drift", "pead", "analyst revisions",
                              "earnings surprise", "estimate revision", "earnings momentum"],
                       "modules": ["pead_factor.py"]},
}

# ── FRONTIER themes the platform does NOT cover yet (a hit here = opportunity) ──
FRONTIER_THEMES = {
    "text_nlp":        ["textual analysis", "10-k sentiment", "nlp finance", "news sentiment",
                        "earnings call", "lazy prices", "language model", "llm"],
    "options_implied": ["implied volatility", "variance risk premium", "option-implied",
                        "volatility skew", "put-call ratio"],
    "short_crowding":  ["short interest", "factor crowding", "arbitrage capacity", "days to cover"],
    "liquidity":       ["liquidity factor", "amihud illiquidity", "bid-ask spread", "turnover liquidity"],
    "seasonality":     ["seasonality", "january effect", "turn of the month", "sell in may"],
    "network":         ["supply chain momentum", "customer-supplier", "economic links", "network effects"],
    "esg_climate":     ["esg factor", "sustainable investing", "climate risk premium", "carbon risk"],
    "microstructure":  ["market microstructure", "order flow", "high frequency trading",
                        "limit order book"],
}

# ── the scouted sample papers (offline seed corpus) ───────────────────────────
SEED_PAPERS = [
    {"title": "Portfolio Selection", "year": 1952, "citations": 40000, "authors": "Markowitz",
     "abstract": "Mean-variance portfolio optimization; diversification and the efficient frontier."},
    {"title": "Capital Asset Prices: A Theory of Market Equilibrium under Conditions of Risk",
     "year": 1964, "citations": 30000, "authors": "Sharpe",
     "abstract": "CAPM: systematic risk beta and capital asset pricing of equilibrium returns."},
    {"title": "Returns to Buying Winners and Selling Losers: Momentum", "year": 1993,
     "citations": 20000, "authors": "Jegadeesh, Titman",
     "abstract": "Cross-sectional momentum: past winners continue to outperform past losers."},
    {"title": "The Cross-Section of Expected Stock Returns", "year": 1992, "citations": 25000,
     "authors": "Fama, French",
     "abstract": "Size and book-to-market value factor explain the cross-section; beta does not."},
    {"title": "Value and Profitability: The Other Side of Value (Gross Profitability)", "year": 2013,
     "citations": 3000, "authors": "Novy-Marx",
     "abstract": "Gross profitability is a quality signal that predicts the cross-section of returns."},
    {"title": "Betting Against Beta", "year": 2014, "citations": 4000, "authors": "Frazzini, Pedersen",
     "abstract": "Low beta safe stocks outperform; leverage constraints and a low-risk anomaly."},
    {"title": "Quality Minus Junk", "year": 2019, "citations": 2000, "authors": "Asness, Frazzini, Pedersen",
     "abstract": "A quality factor from profitability, growth, safety and payout earns alpha in 24 markets."},
    {"title": "Empirical Asset Pricing via Machine Learning", "year": 2020, "citations": 5000,
     "authors": "Gu, Kelly, Xiu",
     "abstract": "Machine learning and neural networks improve the cross-section of expected returns."},
    {"title": "Value Investing Using Financial Statements (Piotroski F-Score)", "year": 2000,
     "citations": 4000, "authors": "Piotroski",
     "abstract": "A fundamental score separates winners from losers among high book-to-market value firms."},
    {"title": "Performance of Quality Factor in Indian Equity Market", "year": 2022, "citations": 20,
     "authors": "Jacob, Pradeep, Varma",
     "abstract": "The QMJ quality factor earns high alpha in India, an emerging market with tunnelling "
                 "and corporate governance concerns; profitability and payout drive the premium."},
]


# ── pure text / scoring core ──────────────────────────────────────────────────
def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9\s\-]", " ", (text or "").lower())


def reconstruct_abstract(inverted_index: dict) -> str:
    """OpenAlex returns abstracts as an inverted index {word: [positions]} — rebuild
    the running text."""
    if not inverted_index:
        return ""
    positions = []
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions.append((i, word))
    return " ".join(w for _, w in sorted(positions))


def _theme_hits(text: str, keywords) -> float:
    """Weighted keyword hits: multi-word phrases count double (more specific)."""
    t = normalise(text)
    score = 0.0
    for kw in keywords:
        if kw in t:
            score += 2.0 if " " in kw else 1.0
    return score


def match_themes(text: str, themes: dict) -> dict:
    """{theme: weight} for every theme with at least one keyword hit."""
    out = {}
    for name, spec in themes.items():
        kws = spec["kw"] if isinstance(spec, dict) else spec
        h = _theme_hits(text, kws)
        if h > 0:
            out[name] = h
    return out


def score_paper(paper: dict, now_year: int | None = None) -> dict:
    """Relevance score in [0,1] blending keyword match, recency and citations, plus
    the covered/frontier theme classification and mapped modules."""
    now_year = now_year or dt.date.today().year
    text = f"{paper.get('title','')} {paper.get('abstract','')}"
    covered = match_themes(text, COVERED_THEMES)
    frontier = match_themes(text, FRONTIER_THEMES)

    kw_total = sum(covered.values()) + sum(frontier.values())
    kw_rel = min(1.0, kw_total / 6.0)                     # saturat ~6 weighted hits
    age = max(0, now_year - int(paper.get("year", now_year)))
    recency = max(0.0, 1.0 - age / 40.0)                  # linear decay over 40y
    cites = float(paper.get("citations", 0) or 0)
    import math
    cite_score = min(1.0, math.log10(cites + 1) / 4.0)    # 10k cites -> 1.0

    score = 0.6 * kw_rel + 0.2 * recency + 0.2 * cite_score

    modules = sorted({m for name in covered for m in COVERED_THEMES[name]["modules"]})
    if covered and frontier:
        coverage = "extends"          # implemented area, with a frontier angle
    elif covered:
        coverage = "covered"
    elif frontier:
        coverage = "gap"              # frontier theme not yet in the platform
    else:
        coverage = "unmapped"
    return {**paper, "score": round(score, 4), "covered_themes": sorted(covered),
            "frontier_themes": sorted(frontier), "modules": modules, "coverage": coverage}


def dedup(papers: list) -> list:
    """Drop duplicates by DOI, else by normalised title."""
    seen, out = set(), []
    for p in papers:
        key = (p.get("doi") or "").lower() or re.sub(r"\s+", " ", normalise(p.get("title", ""))).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(p)
    return out


def rank(papers: list, now_year: int | None = None) -> list:
    scored = [score_paper(p, now_year) for p in dedup(papers)]
    return sorted(scored, key=lambda p: p["score"], reverse=True)


def coverage_summary(ranked: list) -> dict:
    """Per covered-theme paper counts + which frontier themes surfaced (opportunities)."""
    cov = {t: 0 for t in COVERED_THEMES}
    fro = {t: 0 for t in FRONTIER_THEMES}
    for p in ranked:
        for t in p["covered_themes"]:
            cov[t] += 1
        for t in p["frontier_themes"]:
            fro[t] += 1
    return {"covered": cov, "frontier": fro}


# ── network fetchers (governed via apiclient) ─────────────────────────────────
def _require_ok(r, source):
    """Raise (so apiclient backs off / fetch moves on) on any non-200 — OpenAlex
    signals overload with 503 and a rate-limit body, not just 429."""
    if r.status_code != 200:
        raise RuntimeError(f"{source} HTTP {r.status_code} (rate-limited/unavailable)")
    return r


def _openalex(query: str, limit: int) -> list:
    import apiclient
    mailto = os.environ.get("SCOUT_MAILTO")                # polite pool; never hardcoded
    q = urllib.parse.quote(query)
    url = (f"https://api.openalex.org/works?search={q}"
           f"&per-page={min(limit,50)}&sort=relevance_score:desc"
           f"&filter=type:article" + (f"&mailto={urllib.parse.quote(mailto)}" if mailto else ""))
    r = _require_ok(apiclient.http_get("openalex", url, timeout=20, retries=2), "openalex")
    out = []
    for w in r.json().get("results", []):
        out.append({
            "source": "openalex", "title": w.get("title") or "",
            "abstract": reconstruct_abstract(w.get("abstract_inverted_index")),
            "year": w.get("publication_year"), "citations": w.get("cited_by_count", 0),
            "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
            "url": w.get("id"),
            "authors": ", ".join(a["author"]["display_name"]
                                 for a in (w.get("authorships") or [])[:4]),
        })
    return out


def _strip_jats(text: str) -> str:
    """Crossref abstracts are JATS-XML fragments; drop the tags."""
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def _crossref(query: str, limit: int) -> list:
    """Crossref — reliable, keyless fallback when OpenAlex is overloaded."""
    import apiclient
    mailto = os.environ.get("SCOUT_MAILTO")
    q = urllib.parse.quote(query)
    url = (f"https://api.crossref.org/works?query={q}&rows={min(limit,50)}"
           "&select=title,abstract,published,is-referenced-by-count,DOI,author,type"
           + (f"&mailto={urllib.parse.quote(mailto)}" if mailto else ""))
    r = _require_ok(apiclient.http_get("crossref", url, timeout=20, retries=2), "crossref")
    out = []
    for w in r.json().get("message", {}).get("items", []):
        if w.get("type") not in (None, "journal-article", "posted-content", "proceedings-article"):
            continue
        yr = None
        dp = (w.get("published", {}) or {}).get("date-parts", [[None]])
        if dp and dp[0]:
            yr = dp[0][0]
        out.append({
            "source": "crossref", "title": " ".join(w.get("title", []) or []),
            "abstract": _strip_jats(w.get("abstract", "")),
            "year": yr, "citations": w.get("is-referenced-by-count", 0),
            "doi": w.get("DOI", ""), "url": f"https://doi.org/{w.get('DOI','')}",
            "authors": ", ".join(f"{a.get('family','')}"
                                 for a in (w.get("author") or [])[:4]),
        })
    return out


def _arxiv(query: str, limit: int) -> list:
    import xml.etree.ElementTree as ET

    import apiclient
    q = urllib.parse.quote(f"all:{query}")
    url = f"http://export.arxiv.org/api/query?search_query={q}&start=0&max_results={min(limit,50)}"
    r = _require_ok(apiclient.http_get("arxiv", url, timeout=20, retries=2), "arxiv")
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(r.text).findall("a:entry", ns):
        title = (e.findtext("a:title", default="", namespaces=ns) or "").strip()
        summ = (e.findtext("a:summary", default="", namespaces=ns) or "").strip()
        pub = (e.findtext("a:published", default="", namespaces=ns) or "")[:4]
        out.append({"source": "arxiv", "title": title, "abstract": summ,
                    "year": int(pub) if pub.isdigit() else None, "citations": 0,
                    "doi": "", "url": e.findtext("a:id", default="", namespaces=ns),
                    "authors": ", ".join(a.findtext("a:name", default="", namespaces=ns)
                                         for a in e.findall("a:author", ns)[:4])})
    return out


_FETCHERS = {"openalex": _openalex, "crossref": _crossref, "arxiv": _arxiv}


def fetch(query: str, limit: int, sources=("openalex", "crossref", "arxiv")) -> list:
    """Query the scholarly APIs; skip any that error (offline-safe). Crossref is a
    keyless fallback for when OpenAlex is overloaded (503) or arXiv throttles (429)."""
    got = []
    for src in sources:
        try:
            hits = _FETCHERS[src](query, limit)
            got += hits
            print(f"  [scout] {src}: {len(hits)} hits", file=sys.stderr)
        except Exception as e:                             # noqa: BLE001
            print(f"  [scout] {src} unavailable: {e}", file=sys.stderr)
    return got


# ── report ────────────────────────────────────────────────────────────────────
def render_report(ranked: list, summary: dict, query: str | None, top: int = 30) -> str:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"# Literature Scout — global equity-market research", "",
         f"_Generated {now}. Query: **{query or 'seed themes'}**. "
         f"{len(ranked)} papers scored._", "",
         "Seeded from the platform's implemented papers (Markowitz/Sharpe/Fama-French/"
         "Piotroski/Jegadeesh-Titman/Novy-Marx/Frazzini-Pedersen/AFP-QMJ/Gu-Kelly-Xiu/"
         "IIMA-2022). Each hit is scored on keyword relevance + recency + citations, "
         "mapped to the module that implements it, and tagged "
         "`covered` / `extends` / **`gap`** (a frontier theme not yet in the platform).", ""]

    L += ["## Top relevant papers", "",
          "| # | score | year | cites | paper | coverage | maps to |",
          "|---|---|---|---|---|---|---|"]
    for i, p in enumerate(ranked[:top], 1):
        title = (p["title"][:70] + "…") if len(p["title"]) > 71 else p["title"]
        mods = ", ".join(p["modules"][:2]) or ("frontier" if p["coverage"] == "gap" else "—")
        cov = "**gap**" if p["coverage"] == "gap" else p["coverage"]
        L.append(f"| {i} | {p['score']} | {p.get('year','')} | {p.get('citations','')} | "
                 f"{title} ({p.get('authors','')}) | {cov} | {mods} |")

    gaps = [p for p in ranked if p["coverage"] == "gap"]
    L += ["", "## Research gaps / frontier opportunities", "",
          "Papers matching themes the platform does **not** implement yet — candidate "
          "new factors/modules:", ""]
    if gaps:
        L += ["| score | frontier theme(s) | paper |", "|---|---|---|"]
        for p in gaps[:20]:
            L.append(f"| {p['score']} | {', '.join(p['frontier_themes'])} | "
                     f"{p['title'][:70]} ({p.get('authors','')}) |")
    else:
        L.append("_None surfaced in this run._")

    L += ["", "## Coverage summary", "",
          "**Implemented themes** (papers found per theme):", ""]
    for t, n in sorted(summary["covered"].items(), key=lambda kv: -kv[1]):
        L.append(f"- `{t}` — {n} paper(s) → {', '.join(COVERED_THEMES[t]['modules'])}")
    L += ["", "**Frontier themes** (opportunity signal — higher = more active research the "
          "platform is missing):", ""]
    for t, n in sorted(summary["frontier"].items(), key=lambda kv: -kv[1]):
        flag = "  ⟵ opportunity" if n > 0 else ""
        L.append(f"- `{t}` — {n} paper(s){flag}")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default=None, help="free-text query (default: scout seed themes)")
    ap.add_argument("--limit", type=int, default=20, help="results per source per query")
    ap.add_argument("--offline", action="store_true", help="score only the built-in seed corpus")
    ap.add_argument("--gaps", action="store_true", help="print only the research-gap opportunities")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--out", default=None, help="write the markdown report to this path")
    args = ap.parse_args()

    papers = list(SEED_PAPERS)
    if not args.offline:
        queries = [args.query] if args.query else [
            "equity factor investing", "cross-section of stock returns",
            "quality factor emerging markets", "machine learning asset pricing",
            "transaction costs factor strategy"]
        for q in queries:
            papers += fetch(q, args.limit)
        print(f"  scouted {len(papers)} raw hits across {len(queries)} queries",
              file=sys.stderr)

    ranked = rank(papers)
    summary = coverage_summary(ranked)

    if args.gaps:
        gaps = [p for p in ranked if p["coverage"] == "gap"]
        print(f"=== {len(gaps)} research-gap opportunities ===")
        for p in gaps[:args.top]:
            print(f"  [{p['score']}] {', '.join(p['frontier_themes']):28} {p['title'][:60]}")
        return

    report = render_report(ranked, summary, args.query, args.top)
    out = args.out or os.path.join(HERE, "LITERATURE_SCOUT.md")
    open(out, "w").write(report)
    json.dump(ranked[:args.top], open(os.path.splitext(out)[0] + ".json", "w"), indent=2)
    print(f"wrote {out} ({len(ranked)} papers; "
          f"{sum(1 for p in ranked if p['coverage']=='gap')} gaps)")
    # quick console digest
    print("\ntop 10:")
    for i, p in enumerate(ranked[:10], 1):
        print(f"  {i:>2}. [{p['score']}] {p['coverage']:8} {p['title'][:56]}")


if __name__ == "__main__":
    main()

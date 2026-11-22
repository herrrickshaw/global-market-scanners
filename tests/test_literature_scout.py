"""literature_scout.py: theme matching, scoring, ranking, source fetchers (HTTP faked) and the report/CLI."""
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import literature_scout as ls  # noqa: E402


def test_normalise_and_reconstruct():
    assert ls.normalise("Hello, World! 2024-x") == "hello  world  2024-x" and ls.normalise(None) == ""
    assert ls.reconstruct_abstract({"b": [1], "a": [0], "c": [2, 3]}) == "a b c c"
    assert ls.reconstruct_abstract({}) == "" and ls.reconstruct_abstract(None) == ""


def test_theme_hits_weights():
    assert ls._theme_hits("The Momentum factor", ["momentum"]) == 1.0
    assert ls._theme_hits("quality minus junk", ["quality minus junk"]) == 2.0       # phrases count double
    assert ls._theme_hits("quality minus junk momentum", ["quality minus junk", "momentum", "value"]) == 3.0
    assert ls._theme_hits("", ["x"]) == 0.0


def test_match_themes_dict_and_list_specs():
    themes = {"a": {"kw": ["alpha beta"]}, "b": ["gamma"], "c": {"kw": ["zzz"]}}
    assert ls.match_themes("ALPHA beta (and) GAMMA!", themes) == {"a": 2.0, "b": 1.0}      # case-folded, punctuation stripped
    assert ls.match_themes("alpha beta gamma", themes) == {"a": 2.0, "b": 1.0}
    assert ls.match_themes("nothing", themes) == {}


def test_score_paper_components():
    p = {"title": "Quality minus junk", "abstract": "profitability factor", "year": 2024, "citations": 9999}
    r = ls.score_paper(p, now_year=2024)
    # keywords: "quality minus junk"(2) + "profitability factor"(2) + "quality factor"? no; more themes may match
    kw_rel = min(1.0, (sum(ls.match_themes("Quality minus junk profitability factor", ls.COVERED_THEMES).values())) / 6.0)
    expect = 0.6 * kw_rel + 0.2 * 1.0 + 0.2 * min(1.0, np_log10(10000) / 4.0)
    assert r["score"] == pytest.approx(round(expect, 4), abs=1e-4) and r["coverage"] == "covered"
    assert "quality" in r["covered_themes"] and "quality_factor.py" in r["modules"] and r["frontier_themes"] == []
    assert r["title"] == p["title"]                                                   # original keys preserved


def np_log10(x):
    import math
    return math.log10(x)


def test_score_paper_recency_and_citation_bounds():
    base = {"title": "zzz", "abstract": "", "year": 2000, "citations": 0}
    assert ls.score_paper(base, now_year=2040)["score"] == 0.0                         # age 40 -> recency 0
    assert ls.score_paper({**base, "year": 2040}, now_year=2040)["score"] == pytest.approx(0.2)
    assert ls.score_paper({**base, "year": 2000}, now_year=2020)["score"] == pytest.approx(0.2 * 0.5)
    assert ls.score_paper({**base, "year": 2040, "citations": None}, now_year=2040)["score"] == pytest.approx(0.2)
    assert ls.score_paper({**base, "year": 2040, "citations": 10 ** 9}, now_year=2040)["score"] == pytest.approx(0.4)
    assert ls.score_paper({**base, "year": 2040, "citations": 9}, now_year=2040)["score"] == pytest.approx(0.2 + 0.2 * 0.25)
    no_year = ls.score_paper({"title": "zzz"}, now_year=2030)
    assert no_year["score"] == pytest.approx(0.2) and no_year["coverage"] == "unmapped"


def test_coverage_classification(monkeypatch):
    monkeypatch.setattr(ls, "FRONTIER_THEMES", {"quantum": {"kw": ["quantum computing"]}})
    both = ls.score_paper({"title": "quality factor with quantum computing", "year": 2024}, 2024)
    assert both["coverage"] == "extends" and both["frontier_themes"] == ["quantum"]
    gap = ls.score_paper({"title": "quantum computing for pricing", "year": 2024}, 2024)
    assert gap["coverage"] == "gap" and gap["modules"] == []
    assert ls.score_paper({"title": "quality factor", "year": 2024}, 2024)["coverage"] == "covered"


def test_dedup_and_rank():
    papers = [{"title": "A  Paper", "doi": "10.1/X"}, {"title": "other", "doi": "10.1/x"},        # doi case-insensitive
              {"title": "Same Title!"}, {"title": "same   title"}, {"title": ""}, {"title": "unique"}]
    d = ls.dedup(papers)
    assert [p["title"] for p in d] == ["A  Paper", "Same Title!", "unique"]
    ranked = ls.rank([{"title": "quality factor", "year": 2024, "citations": 100}, {"title": "zzz", "year": 1990}], now_year=2024)
    assert ranked[0]["title"] == "quality factor" and ranked[0]["score"] > ranked[1]["score"]


def test_coverage_summary(monkeypatch):
    monkeypatch.setattr(ls, "FRONTIER_THEMES", {"q": {"kw": ["quantum computing"]}})
    ranked = ls.rank([{"title": "quality factor", "year": 2024}, {"title": "quantum computing", "year": 2024}], 2024)
    s = ls.coverage_summary(ranked)
    assert s["covered"]["quality"] == 1 and s["frontier"] == {"q": 1} and set(s["covered"]) == set(ls.COVERED_THEMES)


def test_require_ok():
    ok = types.SimpleNamespace(status_code=200)
    assert ls._require_ok(ok, "x") is ok
    with pytest.raises(RuntimeError, match="openalex HTTP 503"):
        ls._require_ok(types.SimpleNamespace(status_code=503), "openalex")


def _install_http(monkeypatch, fn):
    ac = types.ModuleType("apiclient"); ac.http_get = fn
    monkeypatch.setitem(sys.modules, "apiclient", ac)


def test_openalex(monkeypatch):
    seen = []
    payload = {"results": [{"title": "T1", "abstract_inverted_index": {"hello": [0], "world": [1]}, "publication_year": 2020,
                            "cited_by_count": 5, "doi": "https://doi.org/10.1/abc", "id": "https://openalex.org/W1",
                            "authorships": [{"author": {"display_name": f"A{i}"}} for i in range(6)]},
                           {"title": None, "abstract_inverted_index": None, "doi": None, "authorships": None}]}
    _install_http(monkeypatch, lambda src, url, timeout=None, retries=None: (
        seen.append((src, url, timeout, retries)) or types.SimpleNamespace(status_code=200, json=lambda: payload)))
    monkeypatch.setenv("SCOUT_MAILTO", "me@x.org")
    out = ls._openalex("quality factor", 99)
    src, url, timeout, retries = seen[0]
    assert src == "openalex" and (timeout, retries) == (20, 2) and "per-page=50" in url and "mailto=me%40x.org" in url
    assert "search=quality%20factor" in url and "filter=type:article" in url
    assert out[0] == {"source": "openalex", "title": "T1", "abstract": "hello world", "year": 2020, "citations": 5,
                      "doi": "10.1/abc", "url": "https://openalex.org/W1", "authors": "A0, A1, A2, A3"}
    assert out[1]["title"] == "" and out[1]["authors"] == "" and out[1]["doi"] == "" and out[1]["abstract"] == ""
    monkeypatch.delenv("SCOUT_MAILTO")
    ls._openalex("q", 7)
    assert "mailto" not in seen[1][1] and "per-page=7" in seen[1][1]


def test_strip_jats_and_crossref(monkeypatch):
    assert ls._strip_jats("<jats:p>Hello <b>there</b></jats:p>") == "Hello  there" and ls._strip_jats(None) == ""
    items = [{"type": "journal-article", "title": ["A", "B"], "abstract": "<p>abs</p>", "published": {"date-parts": [[2021, 5]]},
              "is-referenced-by-count": 9, "DOI": "10.2/x", "author": [{"family": f"F{i}"} for i in range(6)]},
             {"type": "book", "title": ["skip"]},
             {"title": [], "published": {"date-parts": [[None]]}, "DOI": "10.3/y"},
             {"type": "posted-content", "title": ["P"], "published": {}}]
    seen = []
    _install_http(monkeypatch, lambda src, url, timeout=None, retries=None: (
        seen.append((src, url)) or types.SimpleNamespace(status_code=200, json=lambda: {"message": {"items": items}})))
    monkeypatch.delenv("SCOUT_MAILTO", raising=False)
    out = ls._crossref("q", 10)
    assert [o["title"] for o in out] == ["A B", "", "P"]                                 # book skipped
    assert out[0]["year"] == 2021 and out[0]["abstract"] == "abs" and out[0]["authors"] == "F0, F1, F2, F3"
    assert out[1]["year"] is None and out[0]["url"] == "https://doi.org/10.2/x" and "rows=10" in seen[0][1]
    assert out[2]["year"] is None and seen[0][0] == "crossref"


ATOM = """<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/1</id><title> Paper One </title><summary> abs </summary><published>2023-01-01T00:00:00Z</published>
<author><name>X</name></author><author><name>Y</name></author></entry>
<entry><id>http://arxiv.org/abs/2</id><title>Two</title><summary></summary><published></published></entry></feed>"""


def test_arxiv(monkeypatch):
    seen = []
    _install_http(monkeypatch, lambda src, url, timeout=None, retries=None: (
        seen.append(url) or types.SimpleNamespace(status_code=200, text=ATOM)))
    out = ls._arxiv("asset pricing", 5)
    assert out[0] == {"source": "arxiv", "title": "Paper One", "abstract": "abs", "year": 2023, "citations": 0, "doi": "",
                      "url": "http://arxiv.org/abs/1", "authors": "X, Y"}
    assert out[1]["year"] is None and out[1]["authors"] == "" and "max_results=5" in seen[0] and "all%3Aasset%20pricing" in seen[0]


def test_fetch_skips_failing_sources(monkeypatch, capsys):
    monkeypatch.setitem(ls._FETCHERS, "openalex", lambda q, n: [{"title": "a"}])
    monkeypatch.setitem(ls._FETCHERS, "crossref", lambda q, n: (_ for _ in ()).throw(RuntimeError("503")))
    monkeypatch.setitem(ls._FETCHERS, "arxiv", lambda q, n: [{"title": "b"}, {"title": "c"}])
    assert [p["title"] for p in ls.fetch("q", 3)] == ["a", "b", "c"]
    err = capsys.readouterr().err
    assert "openalex: 1 hits" in err and "crossref unavailable: 503" in err and "arxiv: 2 hits" in err
    assert ls.fetch("q", 3, sources=("arxiv",)) == [{"title": "b"}, {"title": "c"}]


def test_render_report(monkeypatch):
    monkeypatch.setattr(ls, "FRONTIER_THEMES", {"quantum": {"kw": ["quantum computing"]}, "none": {"kw": ["zzzz"]}})
    papers = [{"title": "x" * 80, "authors": "A", "year": 2024, "citations": 10, "abstract": "quality factor"},
              {"title": "quantum computing paper", "authors": "B", "year": 2023, "citations": 1},
              {"title": "unrelated", "year": 2000}]
    ranked = ls.rank(papers, 2024)
    md = ls.render_report(ranked, ls.coverage_summary(ranked), "my query", top=2)
    assert "Query: **my query**" in md and "3 papers scored" in md and "| # | score | year |" in md
    assert "…" in md and "x" * 70 + "…" in md and "**gap**" in md and "| frontier |" in md
    assert "unrelated" not in md.split("## Research gaps")[0]                          # top=2 truncates the table
    assert "`quantum` — 1 paper(s)  ⟵ opportunity" in md and "`none` — 0 paper(s)\n" in md
    assert "- `quality` — 1 paper(s) → quality_factor.py, dvm_composite.py" in md
    empty = ls.render_report([], ls.coverage_summary([]), None)
    assert "seed themes" in empty and "_None surfaced in this run._" in empty


def test_main_offline_writes_report_and_json(monkeypatch, tmp_path, capsys):
    out = str(tmp_path / "r.md")
    monkeypatch.setattr(sys, "argv", ["ls", "--offline", "--out", out, "--top", "3"])
    ls.main()
    assert os.path.exists(out) and len(json.load(open(str(tmp_path / "r.json")))) == 3
    text = capsys.readouterr().out
    assert f"wrote {out} (10 papers; 0 gaps)" in text and "top 10:" in text
    monkeypatch.setattr(ls, "HERE", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["ls", "--offline"])
    ls.main()
    assert (tmp_path / "LITERATURE_SCOUT.md").exists()


def test_main_online_queries_and_gaps(monkeypatch, capsys):
    monkeypatch.setattr(ls, "FRONTIER_THEMES", {"quantum": {"kw": ["quantum computing"]}})
    calls = []
    monkeypatch.setattr(ls, "fetch", lambda q, n: (calls.append((q, n)), [{"title": "quantum computing study", "year": 2024, "authors": "Z"}])[1])
    monkeypatch.setattr(sys, "argv", ["ls", "--gaps", "--query", "custom", "--limit", "4"])
    ls.main()
    out = capsys.readouterr()
    assert calls == [("custom", 4)] and "scouted 11 raw hits across 1 queries" in out.err
    assert "=== 1 research-gap opportunities ===" in out.out and "quantum" in out.out
    calls.clear()
    monkeypatch.setattr(sys, "argv", ["ls", "--gaps"])
    ls.main()
    assert len(calls) == 5 and calls[0][0] == "equity factor investing" and calls[0][1] == 20

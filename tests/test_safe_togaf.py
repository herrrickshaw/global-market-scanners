"""safe/safe_backlog.py + architecture/togaf.py: governance reports and principle checks."""
import importlib
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "safe"))
sys.path.insert(0, os.path.join(ROOT, "architecture"))

import safe_backlog as sb  # noqa: E402
import togaf  # noqa: E402


@pytest.fixture
def backlog(monkeypatch):
    B = {"art": "Test ART",
         "strategic_themes": [{"id": "ST1", "name": "Theme One"}, {"id": "ST2", "name": "Theme Two"}],
         "epics": [{"id": "E1", "name": "Epic A", "theme": "ST1", "status": "done", "pi": "PI-1"},
                   {"id": "E2", "name": "Epic B", "theme": "ST1", "status": "in_progress", "pi": "PI-2"},
                   {"id": "E3", "name": "Epic C", "theme": "ST2", "status": "backlog", "pi": "PI-2"}],
         "pis": [{"id": "PI-1", "name": "First", "objectives": ["obj one", "obj two"]},
                 {"id": "PI-2", "name": "Second", "objectives": []},
                 {"id": "PI-3", "name": "Empty", "objectives": []}],
         "features": [{"id": "F1", "name": "Feat 1", "pi": "PI-1", "epic": "E1", "status": "done", "size": 3, "module": "m1.py"},
                      {"id": "F2", "name": "Feat 2", "pi": "PI-1", "epic": "E1", "status": "backlog", "size": 5, "module": "m2.py"},
                      {"id": "F3", "name": "Feat 3", "pi": "PI-2", "epic": "E2", "status": "in_progress", "size": 8, "module": "m3.py"},
                      {"id": "F4", "name": "Feat 4", "pi": "PI-2", "epic": "E3", "status": "done", "size": 2, "module": "m4.py"}],
         "kpis": [{"metric": "tests", "value": "42"}, {"metric": "coverage", "value": "80%"}]}
    monkeypatch.setattr(sb, "B", B)
    monkeypatch.setattr(sb, "EPICS", {e["id"]: e for e in B["epics"]})
    monkeypatch.setattr(sb, "THEMES", {t["id"]: t for t in B["strategic_themes"]})
    monkeypatch.setattr(sb, "PIS", {p["id"]: p for p in B["pis"]})
    return B


def test_real_backlog_is_consistent():
    B = json.load(open(os.path.join(ROOT, "safe", "backlog.json")))
    epics = {e["id"] for e in B["epics"]}; pis = {p["id"] for p in B["pis"]}; themes = {t["id"] for t in B["strategic_themes"]}
    assert all(f["epic"] in epics and f["pi"] in pis for f in B["features"])
    assert all(e["theme"] in themes and e["pi"] in pis for e in B["epics"])
    assert all(e["status"] in sb._MARK for e in B["epics"]) and all(f["status"] in sb._MARK for f in B["features"])
    assert all(f["size"] > 0 for f in B["features"])


def test_portfolio(backlog, capsys):
    sb.portfolio()
    out = capsys.readouterr().out
    assert out.startswith("ART: Test ART\n") and "● ST1 — Theme One" in out
    assert "✅ E1 Epic A  [PI-1]" in out and "🔶 E2 Epic B  [PI-2]" in out and "⬜ E3 Epic C  [PI-2]" in out
    assert out.index("ST1") < out.index("E1") < out.index("ST2") < out.index("E3")


def test_pi_detail_and_unknown(backlog, capsys):
    sb.pi("PI-1")
    out = capsys.readouterr().out
    assert "PI-1 — First" in out and "• obj one" in out and "Features (3/8 pts done)" in out
    assert "✅ F1 Feat 1  (3) — m1.py" in out and "⬜ F2 Feat 2  (5) — m2.py" in out and "F3" not in out
    sb.pi("PI-9")
    assert capsys.readouterr().out.strip() == "PIs: PI-1, PI-2, PI-3"
    sb.pi("")
    assert capsys.readouterr().out.startswith("PIs:")


def test_roadmap(backlog, capsys):
    sb.roadmap()
    out = capsys.readouterr().out
    assert "▐ PI-1 — First  [1/2 features]" in out and "▐ PI-2 — Second  [1/2 features]" in out and "▐ PI-3 — Empty  [0/0 features]" in out
    assert out.index("E1 Epic A") < out.index("F1 Feat 1") < out.index("F2 Feat 2")
    assert "🔶 F3 Feat 3 (8)" in out and "✅ F4 Feat 4 (2)" in out


def test_burnup(backlog, capsys):
    sb.burnup()
    lines = capsys.readouterr().out.splitlines()
    pi1 = next(l for l in lines if l.strip().startswith("PI-1"))
    assert pi1.split()[1:3] == ["3", "8"] and "37%" in pi1 and pi1.count("█") == 7             # int(20*3/8)=7
    pi2 = next(l for l in lines if l.strip().startswith("PI-2"))
    assert pi2.split()[1:3] == ["2", "10"] and "20%" in pi2 and pi2.count("█") == 4
    pi3 = next(l for l in lines if l.strip().startswith("PI-3"))
    assert pi3.split()[1:3] == ["0", "0"] and "0%" in pi3 and "█" not in pi3
    assert "overall 27% of 18 story points" in lines[-1]


def test_kpis_and_backlog(backlog, capsys):
    sb.kpis()
    out = capsys.readouterr().out
    assert "Measure & Grow — KPIs" in out and "tests" in out and "42" in out and "80%" in out
    sb.backlog()
    out = capsys.readouterr().out
    assert "F2 Feat 2  [PI-1] (5) — m2.py" in out and "F3 Feat 3  [PI-2] (8)" in out and "F1" not in out and "F4" not in out


def test_cmds_registry():
    assert set(sb.CMDS) == {"portfolio", "roadmap", "burnup", "kpis", "backlog"} and sb.CMDS["kpis"] is sb.kpis


def test_real_backlog_reports_run(capsys):
    for fn in (sb.portfolio, sb.roadmap, sb.burnup, sb.kpis, sb.backlog):
        fn()
    assert "overall 100% of" in capsys.readouterr().out
    first = next(iter(sb.PIS))
    sb.pi(first)
    assert first in capsys.readouterr().out


# ───────────────────────────── togaf ─────────────────────────────
def test_real_repo_passes_each_check():
    for name, fn in togaf.CHECKS.items():
        ok, detail = fn()
        assert isinstance(detail, str) and detail
        if name != "no_secrets":                                                       # git-grep needs a git checkout
            assert ok, (name, detail)


def test_every_principle_has_a_registered_check():
    for p in togaf.A["principles"]:
        assert p["check"] in togaf.CHECKS


def test_chk_no_secrets(monkeypatch):
    class R:
        def __init__(self, out):
            self.stdout = out
    monkeypatch.setattr(togaf, "_sh", lambda c: R("a.py:1:x\n\nb.py:2:y\n"))
    assert togaf.chk_no_secrets() == (False, "2 hardcoded-secret hits (want 0)")
    monkeypatch.setattr(togaf, "_sh", lambda c: R(""))
    assert togaf.chk_no_secrets() == (True, "0 hardcoded-secret hits (want 0)")
    seen = []
    monkeypatch.setattr(togaf, "_sh", lambda c: (seen.append(c), R(""))[1])
    togaf.chk_no_secrets()
    assert "sk-ant-" in seen[0] and "git grep" in seen[0] and "':!*.md'" in seen[0]


def test_sh_runs_in_root(monkeypatch):
    seen = {}
    import subprocess
    monkeypatch.setattr(togaf.subprocess, "run", lambda cmd, **kw: seen.update(cmd=cmd, **kw) or "done")
    assert togaf._sh("echo hi") == "done" and seen["cwd"] == togaf.ROOT and seen["shell"] is True and seen["capture_output"] is True


def _fake_root(tmp_path, monkeypatch, files):
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    monkeypatch.setattr(togaf, "ROOT", str(tmp_path))


def test_file_based_checks_detect_regressions(tmp_path, monkeypatch):
    _fake_root(tmp_path, monkeypatch, {"pit_backtest.py": "cached_download", "factor_research.py": "cached_download",
                                       "ml_viability.py": "nothing", "screen_viability.py": "cached_download"})
    ok, d = togaf.chk_cache_first()
    assert ok is False and "3/4" in d and "ml_viability.py" in d
    _fake_root(tmp_path, monkeypatch, {"market_store.py": "apiclient", "fundamentals_global.py": "apiclient",
                                       "enrich_industries.py": "apiclient", "pit_fundamentals.py": "apiclient"})
    assert togaf.chk_rate_governed() == (True, "fetchers via apiclient: 4/4")
    _fake_root(tmp_path, monkeypatch, {"pit_fundamentals.py": "no filter here"})
    assert togaf.chk_point_in_time() == (False, "no filed-date filter found")
    _fake_root(tmp_path, monkeypatch, {"pit_fundamentals.py": "filed <= asof"})
    assert togaf.chk_point_in_time()[0] is True
    _fake_root(tmp_path, monkeypatch, {"pit_fundamentals.py": "filed only"})
    assert togaf.chk_point_in_time()[0] is False                                       # needs both tokens
    _fake_root(tmp_path, monkeypatch, {"fundamentals_global.py": "np.isfinite", "screen_viability.py": ".clip(-clip, clip)"})
    assert togaf.chk_data_quality()[0] is True
    _fake_root(tmp_path, monkeypatch, {"fundamentals_global.py": "np.isfinite", "screen_viability.py": "nothing"})
    assert togaf.chk_data_quality() == (False, "missing quality guards")


def test_presence_checks(tmp_path, monkeypatch):
    _fake_root(tmp_path, monkeypatch, {"market_store.py": "", "apiclient.py": "", "market_holidays.py": ""})
    assert togaf.chk_reuse() == (False, "shared building blocks present: 3/4")
    (tmp_path / "ml_signal_engine.py").write_text("")
    assert togaf.chk_reuse()[0] is True
    assert togaf.chk_warehouse_present()[0] is False
    (tmp_path / "warehouse.py").write_text("")
    assert togaf.chk_warehouse_present()[0] is True
    assert togaf.chk_docs_present() == (False, "core docs: 0/6")
    for d in ("README.md", "USER_GUIDE.md", "SCHEMA.md", "ARCHITECTURE.md", "ARCHITECTURE_MAP.md", "SECURITY.md"):
        (tmp_path / d).write_text("")
    assert togaf.chk_docs_present() == (True, "core docs: 6/6")


def test_printing_commands(capsys):
    togaf.principles()
    out = capsys.readouterr().out
    assert "Statement:" in out and "Rationale:" in out and "Implications:" in out and f"[{togaf.A['principles'][0]['id']}]" in out
    togaf.adm()
    assert "TOGAF ADM cycle -> platform" in capsys.readouterr().out
    togaf.domains()
    out = capsys.readouterr().out
    assert "Architecture domains (BDAT)" in out and "Business" in out and "Data" in out


def test_govern_pass_fail_and_unknown(monkeypatch, capsys):
    principles = [{"id": "P1", "name": "one", "check": "a"}, {"id": "P2", "name": "two", "check": "b"},
                  {"id": "P3", "name": "three", "check": "nope"}]
    monkeypatch.setattr(togaf, "A", {"principles": principles})
    monkeypatch.setattr(togaf, "CHECKS", {"a": lambda: (True, "fine"), "b": lambda: (False, "broken")})
    assert togaf.govern() is False
    out = capsys.readouterr().out
    assert "✅ P1" in out and "❌ P2" in out and "— P3" in out and "no check" in out and "1/3 principles verified compliant." in out
    monkeypatch.setattr(togaf, "A", {"principles": principles[:1]})
    assert togaf.govern() is True and "1/1 principles" in capsys.readouterr().out


def test_cli_entrypoint_via_subprocess():
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(ROOT, "architecture", "togaf.py"), "domains"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "Architecture domains" in r.stdout
    r = subprocess.run([sys.executable, os.path.join(ROOT, "architecture", "togaf.py"), "bogus"], capture_output=True, text=True, timeout=30)
    assert "commands: principles | adm | domains | govern" in r.stdout

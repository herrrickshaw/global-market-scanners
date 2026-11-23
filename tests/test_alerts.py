"""alerts.py: snapshot diffing, storage, SMTP gating and the CLI flow (offline)."""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts  # noqa: E402


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "STATE", str(tmp_path / "state"))
    monkeypatch.setattr(alerts, "HERE", str(tmp_path))
    return tmp_path


def test_diff_sets_partitions_and_counts():
    d = alerts.diff_sets({"A", "B", "C"}, {"B", "C", "D", "E"})
    assert d["new"] == ["D", "E"]
    assert d["dropped"] == ["A"]
    assert d["retained"] == ["B", "C"]
    assert (d["n_new"], d["n_dropped"], d["n_total"]) == (2, 1, 4)


def test_diff_sets_accepts_lists_and_empty():
    d = alerts.diff_sets([], [])
    assert d == {"new": [], "dropped": [], "retained": [], "n_new": 0, "n_dropped": 0, "n_total": 0}
    assert alerts.diff_sets(["x", "x"], ["x"])["n_total"] == 1


def test_format_alert_header_and_sections():
    d = alerts.diff_sets({"A"}, {"B"})
    out = alerts.format_alert(d, "lab", "2026-01-02")
    lines = out.split("\n")
    assert lines[0] == "[2026-01-02] lab: 1 names (+1 new, -1 dropped)"
    assert lines[1] == "  NEW:     B"
    assert lines[2] == "  DROPPED: A"
    assert len(lines) == 3


def test_format_alert_omits_empty_sections_and_truncates_to_40():
    same = alerts.format_alert(alerts.diff_sets({"A"}, {"A"}), "l", "d")
    assert "NEW" not in same and "DROPPED" not in same
    big = alerts.diff_sets(set(), {f"T{i:03d}" for i in range(50)})
    line = alerts.format_alert(big, "l", "d").split("\n")[1]
    assert line.count(",") == 39  # exactly 40 names


def test_save_and_latest_prior_strictly_before(state):
    p1 = alerts.save_snapshot("g", {"B", "A"}, "2026-01-01")
    alerts.save_snapshot("g", {"C"}, "2026-01-03")
    assert json.load(open(p1)) == ["A", "B"]
    assert alerts.latest_prior("g", "2026-01-03") == {"A", "B"}   # same date excluded
    assert alerts.latest_prior("g", "2026-01-04") == {"C"}
    assert alerts.latest_prior("g", "2026-01-01") is None
    assert alerts.latest_prior("other", "2030-01-01") is None


def _make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE dvm_composite(market TEXT, ticker TEXT, code TEXT)")
    con.executemany("INSERT INTO dvm_composite VALUES (?,?,?)", rows)
    con.commit()
    con.close()


def test_ggg_members_filters_code(tmp_path):
    db = str(tmp_path / "d.db")
    _make_db(db, [("US", "AAPL", "GGG"), ("US", "XOM", "GGB"), ("IN", "TCS", "GGG")])
    assert alerts.ggg_members(db) == {"US:AAPL", "IN:TCS"}


def test_send_email_noop_without_env(monkeypatch, capsys):
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
        monkeypatch.delenv(k, raising=False)
    assert alerts.send_email("a@b.c", "s", "b") is False
    assert "not set" in capsys.readouterr().err


@pytest.mark.parametrize("missing", ["SMTP_HOST", "SMTP_USER", "SMTP_PASS"])
def test_send_email_requires_all_three(monkeypatch, missing):
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv(missing)
    assert alerts.send_email("a@b.c", "s", "b") is False


def test_send_email_sends_with_env(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port):
            calls.append(("init", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            calls.append(("tls",))

        def login(self, u, p):
            calls.append(("login", u, p))

        def sendmail(self, frm, to, msg):
            calls.append(("send", frm, to, msg))

    monkeypatch.setattr(alerts.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "h")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASS", "p")
    monkeypatch.setenv("SMTP_PORT", "2525")
    assert alerts.send_email("to@x", "subj", "body") is True
    assert calls[0] == ("init", "h", 2525)
    assert calls[1] == ("tls",) and calls[2] == ("login", "u", "p")
    assert calls[3][1] == "u" and calls[3][2] == ["to@x"]
    assert "Subject: subj" in calls[3][3] and "To: to@x" in calls[3][3]


def test_send_email_default_port_587(monkeypatch):
    ports = []

    class FakeSMTP:
        def __init__(self, host, port):
            ports.append(port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self): pass
        def login(self, *a): pass
        def sendmail(self, *a): pass

    monkeypatch.setattr(alerts.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "h")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASS", "p")
    monkeypatch.delenv("SMTP_PORT", raising=False)
    alerts.send_email("t", "s", "b")
    assert ports == [587]


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["alerts.py", *argv])
    alerts.main()


def test_main_no_flags_prints_help(state, monkeypatch, capsys):
    _run(monkeypatch)
    assert "--snapshot" in capsys.readouterr().out


def test_main_missing_db_exits(state, monkeypatch):
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, "--snapshot", "--db", str(state / "nope.db"))
    assert "dvm_composite.db" in str(e.value)


def test_main_baseline_then_diff_then_email(state, monkeypatch, capsys):
    db = str(state / "d.db")
    _make_db(db, [("US", "A", "GGG"), ("US", "B", "GGG")])
    _run(monkeypatch, "--snapshot", "--db", db, "--date", "2026-02-01", "--label", "L")
    out = capsys.readouterr()
    assert "no prior snapshot" in out.out
    assert not (state / "alert_L_2026-02-01.json").exists()

    con = sqlite3.connect(db)
    con.execute("DELETE FROM dvm_composite WHERE ticker='A'")
    con.execute("INSERT INTO dvm_composite VALUES ('US','C','GGG')")
    con.commit(); con.close()
    sent = []
    monkeypatch.setattr(alerts, "send_email", lambda to, s, b: sent.append((to, s, b)) or True)
    _run(monkeypatch, "--snapshot", "--db", db, "--date", "2026-02-02", "--label", "L",
         "--email", "me@x")
    out = capsys.readouterr()
    diff = json.load(open(state / "alert_L_2026-02-02.json"))
    assert diff["new"] == ["US:C"] and diff["dropped"] == ["US:A"]
    assert "NEW:     US:C" in out.out
    assert sent[0][0] == "me@x" and sent[0][1] == "[L] 1 new / 1 dropped (2026-02-02)"
    assert "email sent" in out.err


def test_main_email_skipped_message(state, monkeypatch, capsys):
    db = str(state / "d.db")
    _make_db(db, [("US", "A", "GGG")])
    alerts.save_snapshot("L", {"US:Z"}, "2026-01-01")
    monkeypatch.setattr(alerts, "send_email", lambda *a: False)
    _run(monkeypatch, "--snapshot", "--db", db, "--date", "2026-01-02", "--label", "L", "--email", "x@y")
    assert "email skipped" in capsys.readouterr().err


def test_main_history_lists_snapshots(state, monkeypatch, capsys):
    alerts.save_snapshot("L", {"a", "b", "c"}, "2026-01-01")
    _run(monkeypatch, "--history")
    out = capsys.readouterr().out
    assert "L_2026-01-01.json" in out and "3 names" in out


def test_format_alert_dropped_list_truncated_to_40():
    d = alerts.diff_sets({f"T{i:03d}" for i in range(50)}, set())
    line = alerts.format_alert(d, "l", "d").split("\n")[1]
    assert line.startswith("  DROPPED: ") and line.count(",") == 39

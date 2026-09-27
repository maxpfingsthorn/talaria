import os
import sqlite3

import pytest

from talaria import rehearse
from talaria.containers import HelperError
from talaria.images import RevisionMismatch
from talaria.shell import CommandError, Result
from tests.fakes import make_test_ctx

IMG = {"tag": "v2026.9.24", "id": "sha256:new", "ref": "x@sha256:d", "digest": "sha256:d"}
CUR = {"tag": "v2026.8.3", "id": "sha256:cur", "ref": "x@sha256:c", "digest": "sha256:c"}


def test_copy_data_does_not_follow_symlink(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (tmp_path / "outside").write_text("secret")
    os.symlink(tmp_path / "outside", src / "link")
    (src / ".cache").mkdir()
    (src / ".cache/x").write_text("x")
    (src / "a.db-wal").write_text("w")
    rehearse.copy_data(src, dst, (".cache",))
    assert os.path.islink(dst / "link")
    assert not (dst / ".cache").exists() and not (dst / "a.db-wal").exists()


def test_copy_data_copies_sqlite_consistently(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    c = sqlite3.connect(src / "state.db")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE t (x)")
    c.execute("INSERT INTO t VALUES (1)")
    c.commit()          # data may still live only in the WAL
    rehearse.copy_data(src, dst, ())
    c.close()
    assert sqlite3.connect(dst / "state.db").execute("SELECT x FROM t").fetchall() == [(1,)]


@pytest.fixture
def happy(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    (ctx.conf.data_dir / "config.yaml").write_text("_config_version: 27\n")
    results = {
        "migrate.py": {"ok": True, "before": 27, "after": 30, "latest": 30,
                       "messages": ["✓ Turned off verify-on-stop"], "error": None},
        "dbopen.py": {"ok": True, "before": 25, "after": 28, "schema_version": 30, "error": None},
        "confdiff.py": {"ok": True, "changed": [["model.name", "a", "b"]], "added": [],
                        "removed": [], "error": None},
    }
    calls = []
    monkeypatch.setattr(rehearse, "pull_verify", lambda ctx, tag, commit: dict(IMG))
    monkeypatch.setattr(rehearse, "run_helper",
                        lambda ctx, rec, script, data, work, args=(): (
                            calls.append((rec["id"], script, args)), results[script])[1])
    monkeypatch.setattr(rehearse, "run_doctor",
                        lambda ctx, rec, data: "✓ ok\n" if rec["id"] == "sha256:cur"
                        else "✓ ok\n✗ new problem\n")
    ctx.results, ctx.helper_calls = results, calls
    return ctx


def st_with_current():
    import copy
    from talaria import state
    st = copy.deepcopy(state.DEFAULT)
    st["current"] = dict(CUR)
    return st


def test_rehearse_sets_pending_and_reports(happy):
    st = st_with_current()
    rehearse.rehearse(happy, st, "v2026.9.24", "c0ffee")
    p = st["pending"]
    assert p["tag"] == "v2026.9.24" and p["cfg_after"] == 30 and p["image"] == IMG
    msg = happy.notify.sent[-1]
    assert "v2026.9.24" in msg.text and "27 → 30" in msg.text
    assert "25 → 28 (held back" in msg.text
    assert msg.commands == ["/approve v2026.9.24", "/reject v2026.9.24"]
    joined = "\n".join(msg.untrusted)
    assert "Turned off verify-on-stop" in joined and "~ model.name: a → b" in joined
    assert "+ ✗ new problem" in joined
    assert [c[1] for c in happy.helper_calls] == ["migrate.py", "dbopen.py", "confdiff.py"]
    assert happy.helper_calls[2][2] == ["/opt/talaria-out/config.orig.yaml", "/opt/data/config.yaml"]
    assert not any(happy.paths.staging.iterdir())       # copy deleted


def test_rehearse_never_touches_production(happy):
    before = (happy.conf.data_dir / "config.yaml").read_text()
    rehearse.rehearse(happy, st_with_current(), "v2026.9.24", "c0ffee")
    assert (happy.conf.data_dir / "config.yaml").read_text() == before
    assert happy.sh.calls == []                          # no systemctl, nothing stopped


def test_rehearse_replacing_pending_says_so(happy):
    st = st_with_current()
    st["pending"] = {"tag": "v2026.9.7"}
    rehearse.rehearse(happy, st, "v2026.9.24", "c0ffee")
    assert "Replaces the pending v2026.9.7" in happy.notify.sent[-1].text


def test_migration_failure_is_permanent_with_details(happy):
    happy.results["migrate.py"] = {"ok": False, "before": 27, "after": 27, "latest": 30,
                                   "messages": ["✗ step broke"], "error": "boom"}
    with pytest.raises(rehearse.Permanent) as e:
        rehearse.rehearse(happy, st_with_current(), "v2026.9.24", "c0ffee")
    assert "boom" in str(e.value) and e.value.details == ["✗ step broke"]
    assert not any(happy.paths.staging.iterdir())


def test_dbopen_failure_is_permanent(happy):
    happy.results["dbopen.py"] = {"ok": False, "error": "corrupt"}
    with pytest.raises(rehearse.Permanent, match="corrupt"):
        rehearse.rehearse(happy, st_with_current(), "v2026.9.24", "c0ffee")


def test_helper_crash_is_permanent(happy, monkeypatch):
    def boom(*a, **k):
        raise HelperError("no result")
    monkeypatch.setattr(rehearse, "run_helper", boom)
    with pytest.raises(rehearse.Permanent, match="no result"):
        rehearse.rehearse(happy, st_with_current(), "v2026.9.24", "c0ffee")


def test_revision_mismatch_is_permanent(happy, monkeypatch):
    def bad(*a):
        raise RevisionMismatch("wrong")
    monkeypatch.setattr(rehearse, "pull_verify", bad)
    with pytest.raises(rehearse.Permanent):
        rehearse.rehearse(happy, st_with_current(), "v2026.9.24", "c0ffee")


def test_pull_failure_is_transient(happy, monkeypatch):
    def down(*a):
        raise CommandError(["podman", "pull"], Result(125, "", "network down"))
    monkeypatch.setattr(rehearse, "pull_verify", down)
    with pytest.raises(rehearse.Transient, match="network down"):
        rehearse.rehearse(happy, st_with_current(), "v2026.9.24", "c0ffee")


def test_no_space_is_transient(happy, monkeypatch):
    from talaria import disk
    monkeypatch.setattr(disk, "free_bytes", lambda p: 0)
    with pytest.raises(rehearse.Transient, match="GB"):
        rehearse.rehearse(happy, st_with_current(), "v2026.9.24", "c0ffee")


def test_no_config_skips_diff(happy):
    (happy.conf.data_dir / "config.yaml").unlink()
    rehearse.rehearse(happy, st_with_current(), "v2026.9.24", "c0ffee")
    assert "confdiff.py" not in [c[1] for c in happy.helper_calls]

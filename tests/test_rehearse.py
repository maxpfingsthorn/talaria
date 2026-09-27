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
    monkeypatch.setattr(rehearse, "pull_verify", lambda c, tag, commit: c is ctx and dict(IMG))
    monkeypatch.setattr(rehearse, "run_helper",
                        lambda c, rec, script, data, work, args=(): (
                            calls.append((rec["id"], script, args, c is ctx, data, work,
                                          oct(work.stat().st_mode & 0o777))), results[script])[1])
    monkeypatch.setattr(rehearse, "run_doctor",
                        lambda c, rec, data: (c is ctx and data.name == "data") and "✓ ok\n" if rec["id"] == "sha256:cur"
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
    assert all(c[3] and c[4].name == "data" and c[4].parent == c[5] and c[6] == "0o700"
               for c in happy.helper_calls)
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


# ---- exact behaviour (mutation testing) ----

def test_report_and_pending_exact(happy):
    st = st_with_current()
    rehearse.rehearse(happy, st, "v2026.9.24", "c0ffee")
    assert st["pending"] == {"tag": "v2026.9.24", "image": IMG, "cfg_after": 30, "report": {
        "tag": "v2026.9.24", "digest": "sha256:d", "cfg_before": 27, "cfg_after": 30,
        "db": happy.results["dbopen.py"], "messages": ["✓ Turned off verify-on-stop"],
        "diff": happy.results["confdiff.py"], "doctor": ["+ ✗ new problem"]}}


def test_candidate_message_exact(happy):
    st = st_with_current()
    rehearse.rehearse(happy, st, "v2026.9.24", "c0ffee")
    m = happy.notify.sent[-1]
    assert m.text == ("Hermes v2026.9.24 is ready to deploy (current v2026.8.3). The rehearsal on a "
                      "copy passed.\n"
                      "Release notes: https://github.com/NousResearch/hermes-agent/releases/tag/"
                      "v2026.9.24\n"
                      "Config version: 27 → 30\n"
                      "state.db: 25 → 28 (held back; image supports 30)")
    assert m.untrusted == ["✓ Turned off verify-on-stop", "~ model.name: a → b", "+ ✗ new problem"]


def test_candidate_message_variants(happy):
    happy.conf.hermes_repo = "https://git.example/hermes"
    report = {"tag": "v2", "cfg_before": 1, "cfg_after": 2,
              "db": {"before": 30, "after": 30, "schema_version": 30},
              "messages": ["a", "b"], "diff": {"changed": [], "added": [["k", 1]], "removed": [["r", 2]]},
              "doctor": ["+ x", "- y"]}
    m = rehearse.candidate_message(happy, {"current": None}, report, "v1")
    assert m.text == ("Hermes v2 is ready to deploy (current unknown). The rehearsal on a copy "
                      "passed.\nConfig version: 1 → 2\nstate.db: 30 → 30\nReplaces the pending v1.")
    assert m.untrusted == ["a\nb", "+ k: 1\n- r: 2", "+ x\n- y"]


def test_candidate_message_no_db_and_empty_blocks(happy):
    report = {"tag": "v2", "cfg_before": 1, "cfg_after": 1,
              "db": {"before": None, "after": None, "schema_version": 30},
              "messages": [], "diff": {}, "doctor": []}
    m = rehearse.candidate_message(happy, {}, report, None)
    assert m.text.endswith("state.db: None → None") and m.untrusted == []
    report["db"] = {"before": 3, "after": 3, "schema_version": None}
    assert rehearse.candidate_message(happy, {}, report, None).text.endswith("state.db: 3 → 3")


def test_doctor_changes_rules():
    before = "same\n\n  \nold\n"
    after = "same\n\n  \nnew\n" + "".join(f"n{i}\n" for i in range(45))
    out = rehearse._doctor_changes(before, after)
    assert out[:2] == ["+ new", "+ n0"] and len(out) == 40
    assert rehearse._doctor_changes("a\nb\n", "a\n") == ["- b"]


def test_fmt_diff_partial_keys():
    assert rehearse._fmt_diff({"removed": [["a", 1]]}) == "- a: 1"
    assert rehearse._fmt_diff({"changed": [["a", 1, 2]], "added": [["b", 3]]}) == "~ a: 1 → 2\n+ b: 3"


def test_config_copy_passed_to_confdiff(happy):
    st = st_with_current()
    seen = {}
    real = rehearse.run_helper

    def spy(c, rec, script, data, work, args=()):
        if script == "confdiff.py":
            seen["orig"] = (work / "config.orig.yaml").read_text()
        return real(c, rec, script, data, work, args)

    rehearse.run_helper = spy
    try:
        rehearse.rehearse(happy, st, "v2026.9.24", "c0ffee")
    finally:
        rehearse.run_helper = real
    assert seen["orig"] == "_config_version: 27\n"


def test_no_current_skips_first_doctor(happy, monkeypatch):
    runs = []
    monkeypatch.setattr(rehearse, "run_doctor", lambda c, rec, data: (runs.append(rec["id"]), "")[1])
    st = st_with_current()
    st["current"] = None
    rehearse.rehearse(happy, st, "v2026.9.24", "c0ffee")
    assert runs == ["sha256:new"]


def test_same_tag_is_not_replaced(happy):
    st = st_with_current()
    st["pending"] = {"tag": "v2026.9.24"}
    rehearse.rehearse(happy, st, "v2026.9.24", "c0ffee")
    assert "Replaces" not in happy.notify.sent[-1].text


def test_permanent_message_from_revision(happy, monkeypatch):
    monkeypatch.setattr(rehearse, "pull_verify",
                        lambda *a: (_ for _ in ()).throw(RevisionMismatch("wrong commit")))
    with pytest.raises(rehearse.Permanent) as e:
        rehearse.rehearse(happy, st_with_current(), "v2026.9.24", "c0ffee")
    assert str(e.value) == "wrong commit" and e.value.details == []


def test_failure_texts_exact(happy):
    happy.results["dbopen.py"] = {"ok": False, "error": "corrupt"}
    with pytest.raises(rehearse.Permanent) as e:
        rehearse.rehearse(happy, st_with_current(), "v2026.9.24", "c0ffee")
    assert str(e.value) == "state.db could not be opened: corrupt"
    happy.results["migrate.py"] = {"ok": False, "error": "boom", "messages": []}
    with pytest.raises(rehearse.Permanent) as e:
        rehearse.rehearse(happy, st_with_current(), "v2026.9.24", "c0ffee")
    assert str(e.value) == "config migration failed: boom"


def test_transient_pull_text_exact(happy, monkeypatch):
    def down(*a):
        raise CommandError(["podman", "pull"], Result(125, "", "network down"))
    monkeypatch.setattr(rehearse, "pull_verify", down)
    with pytest.raises(rehearse.Transient) as e:
        rehearse.rehearse(happy, st_with_current(), "v2026.9.24", "c0ffee")
    assert str(e.value) == "pull failed: podman pull … exited 125: network down"


def test_space_needs_the_data_size(happy, monkeypatch):
    from talaria import disk
    size = disk.dir_size(happy.conf.data_dir)
    monkeypatch.setattr(disk, "free_bytes", lambda p: size - 1)
    with pytest.raises(rehearse.Transient):
        rehearse.rehearse(happy, st_with_current(), "v2026.9.24", "c0ffee")


# ---- review I6: live data dir changing or unreadable during the copy ----

def test_copy_data_skips_file_that_vanishes(tmp_path, monkeypatch):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "gone.txt").write_text("x")
    (src / "stays.txt").write_text("y")
    real = os.lstat
    monkeypatch.setattr(rehearse.os, "lstat",
                        lambda p, *a, **k: (_ for _ in ()).throw(FileNotFoundError(p))
                        if str(p).endswith("gone.txt") else real(p, *a, **k))
    rehearse.copy_data(src, dst, ())
    assert (dst / "stays.txt").exists() and not (dst / "gone.txt").exists()


def test_unreadable_file_is_transient_and_cleaned_up(happy):
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    f = happy.conf.data_dir / "locked"
    f.write_text("x")
    f.chmod(0)
    try:
        with pytest.raises(rehearse.Transient) as e:
            rehearse.rehearse(happy, st_with_current(), "v2026.9.24", "c0ffee")
    finally:
        f.chmod(0o600)
    assert str(e.value).startswith("could not copy the data dir: ")
    assert len(str(e.value)) <= 330
    assert not any(happy.paths.staging.iterdir())


def test_candidate_message_has_buttons(happy):
    rehearse.rehearse(happy, st_with_current(), "v2026.9.24", "c0ffee")
    assert happy.notify.sent[-1].buttons == [[("Approve v2026.9.24", "ap:v2026.9.24"),
                                              ("Reject", "rj:v2026.9.24")]]

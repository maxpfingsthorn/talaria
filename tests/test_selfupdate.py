import json

import pytest

from talaria import lock, selfupdate
from talaria.shell import CommandError
from tests.fakes import make_test_ctx


def test_self_update_checks_out_and_runs_the_new_setup(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git").on(str(ctx.paths.bin_link))
    assert selfupdate.self_update(ctx, "v0.2.0") == 0
    d = str(ctx.paths.install_dir)
    assert ctx.sh.calls == [["git", "-C", d, "rev-parse", "HEAD"],
                            ["git", "-C", d, "fetch", "-q", "--tags", "origin"],
                            ["git", "-C", d, "checkout", "-q", "v0.2.0"],
                            [str(ctx.paths.bin_link), "setup", "--as-service", "--lock-held"]]
    assert not ctx.sh.called("systemctl")       # an app install has no bot to restart


def test_self_update_unknown_tag(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git", "-C", str(ctx.paths.install_dir), "fetch")
    ctx.sh.on("git", "-C", str(ctx.paths.install_dir), "rev-parse")
    ctx.sh.on("git", "-C", str(ctx.paths.install_dir), "checkout", rc=1, err="no such ref")
    assert selfupdate.self_update(ctx, "v9.9.9") == 1
    assert "no such ref" in capsys.readouterr().err


def test_self_update_exact(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git").on(str(ctx.paths.bin_link), out="OK: x\n")
    assert selfupdate.self_update(ctx, "v0.2.0") == 0
    assert capsys.readouterr().out == "OK: x\nTalaria v0.2.0 installed.\n"
    assert ctx.sh.timeouts == [None, 600, None, 1800]


def test_self_update_setup_failure_is_reported(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git").on(str(ctx.paths.bin_link), rc=10, out="ACTION REQUIRED: x\n")
    assert selfupdate.self_update(ctx, "v0.2.0") == 1
    assert capsys.readouterr().out == "ACTION REQUIRED: x\n"


def test_self_update_fetch_failure(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git", rc=128, err="offline")
    assert selfupdate.self_update(ctx, "v0.2.0") == 1
    assert len(ctx.sh.calls) == 1 and "offline" in capsys.readouterr().err


def test_self_update_refuses_while_an_operation_runs(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    with lock.op_lock(ctx.paths):
        assert selfupdate.self_update(ctx, "v0.2.0") == 75
    assert ctx.sh.calls == []            # nothing fetched, nothing checked out
    assert capsys.readouterr().err == ("self-update to v0.2.0 refused: an operation is "
                                       "running\n")


# ---- dry run ----

def quadlet_reply(text):
    return json.dumps({"v": 1, "kind": "reply", "text": text, "buttons": []}) + "\n"


@pytest.fixture
def dry(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    wt = ctx.paths.state_dir / "dry-run-fixed"
    ctx.sh.on("git")
    monkeypatch.setattr(selfupdate.tempfile, "mkdtemp",
                        lambda prefix, dir: (wt.mkdir(parents=True, exist_ok=True), str(wt))[1])
    ctx.paths.quadlet.parent.mkdir(parents=True)
    ctx.paths.quadlet.write_text("OLD\n")
    return ctx, wt


def test_dry_run_commands_exact(dry):
    ctx, wt = dry
    ctx.sh.on(str(wt / "bin/talaria"), out="noise\n" + quadlet_reply("OLD\n"))
    assert selfupdate.dry_run(ctx, "v0.6.0") is False
    d = str(ctx.paths.install_dir)
    assert list(zip(ctx.sh.calls, ctx.sh.timeouts)) == [
        (["git", "-C", d, "fetch", "-q", "--tags", "origin"], 600),
        (["git", "-C", d, "worktree", "prune"], None),
        (["git", "-C", d, "worktree", "add", "-q", "--detach", str(wt), "v0.6.0"], 120),
        ([str(wt / "bin/talaria"), "op", "quadlet"], 120),
        (["git", "-C", d, "worktree", "remove", "--force", str(wt)], None)]


def test_dry_run_reports_a_changed_quadlet(dry):
    ctx, wt = dry
    ctx.sh.on(str(wt / "bin/talaria"), out=quadlet_reply("NEW\n"))
    assert selfupdate.dry_run(ctx, "v0.6.0") is True


def test_dry_run_without_an_installed_quadlet_restarts(dry):
    ctx, wt = dry
    ctx.paths.quadlet.unlink()
    ctx.sh.on(str(wt / "bin/talaria"), out=quadlet_reply("NEW\n"))
    assert selfupdate.dry_run(ctx, "v0.6.0") is True


def test_dry_run_removes_the_worktree_when_the_new_version_fails(dry):
    ctx, wt = dry
    ctx.sh.on(str(wt / "bin/talaria"), rc=2, err="talaria op: not allowed")
    with pytest.raises(CommandError):
        selfupdate.dry_run(ctx, "v0.6.0")
    assert ctx.sh.calls[-1][-3:] == ["remove", "--force", str(wt)]


def test_dry_run_without_a_reply_is_an_error(dry):
    ctx, wt = dry
    ctx.sh.on(str(wt / "bin/talaria"), out="")
    with pytest.raises(ValueError, match="Talaria v0.6.0 did not render a Quadlet"):
        selfupdate.dry_run(ctx, "v0.6.0")


def test_dry_run_clears_a_leftover_worktree(dry):
    ctx, wt = dry
    (wt / "junk").mkdir(parents=True)
    ctx.sh.on(str(wt / "bin/talaria"), out=quadlet_reply("OLD\n"))
    selfupdate.dry_run(ctx, "v0.6.0")
    assert not (wt / "junk").exists()

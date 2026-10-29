import json
import subprocess

import pytest

from talaria import lock, selfupdate, trust
from talaria.shell import CommandError, Result
from tests.fakes import make_test_ctx


def test_self_update_checks_out_and_runs_the_new_setup(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git").on(str(ctx.paths.bin_link))
    assert selfupdate.self_update(ctx, "v0.2.0") == 0
    d = str(ctx.paths.install_dir)
    assert ctx.sh.calls == [["git", "-C", d, "rev-parse", "HEAD"],
                            ["git", "-C", d, "fetch", "-q", "origin", "+refs/tags/v0.2.0:refs/tags/v0.2.0"],
                            ["git", "-C", d, "checkout", "-q", "v0.2.0"],
                            [str(ctx.paths.bin_link), "setup", "--as-service", "--lock-held"]]
    assert not ctx.sh.called("systemctl")       # an app install has no bot to restart


def test_self_update_refuses_an_untrusted_tag(tmp_path, capsys, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git").on(str(ctx.paths.bin_link))
    d = str(ctx.paths.install_dir)
    seen = []

    def check(sh, install_dir, tag, tmp_dir=None):
        seen.append((sh, install_dir, tag, tmp_dir, list(ctx.sh.calls)))
        raise trust.Untrusted("bad sig")
    monkeypatch.setattr(trust, "check", check)
    assert selfupdate.self_update(ctx, "v0.8.1") == 1
    assert capsys.readouterr().err == ("self-update refused: v0.8.1 is not signed by a trusted "
                                       "key (bad sig)\n")
    assert len(seen) == 1
    sh, install_dir, tag, tmp_dir, before = seen[0]
    assert (sh, install_dir, tag, tmp_dir) == (ctx.sh, d, "v0.8.1", ctx.paths.state_dir)
    assert before == [["git", "-C", d, "rev-parse", "HEAD"],
                      ["git", "-C", d, "fetch", "-q", "origin",
                       "+refs/tags/v0.8.1:refs/tags/v0.8.1"]]       # checked after the fetch
    assert not ctx.sh.called("git", "-C", d, "checkout")
    assert not ctx.sh.called(str(ctx.paths.bin_link))              # setup never ran


def test_self_update_trusted_tag_is_checked_before_checkout(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git").on(str(ctx.paths.bin_link))
    monkeypatch.setattr(trust, "check", lambda *a, **k: ctx.sh.calls.append(["CHECK"]))
    assert selfupdate.self_update(ctx, "v0.8.1") == 0
    assert [c[0] if c[0] == "CHECK" else c[3] for c in ctx.sh.calls[:4]] == [
        "rev-parse", "fetch", "CHECK", "checkout"]


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


def _setup_fn(ctx, results, events=None):
    """Successive results of the app's setup: a Result, or an exception to raise."""
    it = iter(results)

    def fn(argv, input):
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    return fn


def test_failed_update_rerenders_with_old_code(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git", out="abc\n")
    ctx.sh.on(str(ctx.paths.bin_link), fn=_setup_fn(ctx, [Result(1, "boom\n"), Result(0, "OK\n")]))
    assert selfupdate.self_update(ctx, "v0.6.1") == 1
    d = str(ctx.paths.install_dir)
    assert ctx.sh.calls[-2:] == [["git", "-C", d, "checkout", "-q", "abc"],
                                 [str(ctx.paths.bin_link), "setup", "--as-service", "--lock-held"]]
    assert ctx.sh.timeouts[-1] == 1800
    err = capsys.readouterr().err
    assert err.strip() == "self-update to v0.6.1 failed; previous version restored"


def test_failed_update_and_failed_rerender(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git", out="abc\n")
    ctx.sh.on(str(ctx.paths.bin_link), fn=_setup_fn(ctx, [Result(1), Result(1)]))
    assert selfupdate.self_update(ctx, "v0.6.1") == 1
    err = capsys.readouterr().err
    assert "previous version restored, but its setup failed too" in err
    assert "may be stopped" in err and "/status" in err


def test_setup_timeout_restores(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git", out="abc\n")
    ctx.sh.on(str(ctx.paths.bin_link), fn=_setup_fn(ctx, [
        subprocess.TimeoutExpired("x", 1800), Result(0)]))
    assert selfupdate.self_update(ctx, "v0.6.1") == 1
    d = str(ctx.paths.install_dir)
    assert ctx.sh.calls[-2:] == [["git", "-C", d, "checkout", "-q", "abc"],
                                 [str(ctx.paths.bin_link), "setup", "--as-service", "--lock-held"]]
    assert "previous version restored" in capsys.readouterr().err


def test_rerender_timeout_counts_as_failed(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git", out="abc\n")
    ctx.sh.on(str(ctx.paths.bin_link), fn=_setup_fn(ctx, [
        subprocess.TimeoutExpired("x", 1800), subprocess.TimeoutExpired("x", 1800)]))
    assert selfupdate.self_update(ctx, "v0.6.1") == 1
    assert "its setup failed too" in capsys.readouterr().err


def test_fetch_uses_forced_single_tag(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git").on(str(ctx.paths.bin_link))
    selfupdate.self_update(ctx, "v0.6.1")
    d = str(ctx.paths.install_dir)
    assert ["git", "-C", d, "fetch", "-q", "origin",
            "+refs/tags/v0.6.1:refs/tags/v0.6.1"] in ctx.sh.calls
    assert not any("--tags" in c for c in ctx.sh.calls)


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
        (["git", "-C", d, "fetch", "-q", "origin", "+refs/tags/v0.6.0:refs/tags/v0.6.0"], 600),
        (["git", "-C", d, "worktree", "prune"], None),
        (["git", "-C", d, "worktree", "add", "-q", "--detach", str(wt), "v0.6.0"], 120),
        ([str(wt / "bin/talaria"), "op", "quadlet"], 120),
        (["git", "-C", d, "worktree", "remove", "--force", str(wt)], None)]


def test_dry_run_refuses_before_the_worktree(dry, monkeypatch):
    ctx, wt = dry
    d = str(ctx.paths.install_dir)
    seen = []

    def check(sh, install_dir, tag, tmp_dir=None):
        seen.append((sh, install_dir, tag, tmp_dir, len(ctx.sh.calls)))
        raise trust.Untrusted("bad sig")
    monkeypatch.setattr(trust, "check", check)
    with pytest.raises(trust.Untrusted, match="^bad sig$"):
        selfupdate.dry_run(ctx, "v0.6.0")
    assert seen == [(ctx.sh, d, "v0.6.0", ctx.paths.state_dir, 1)]    # right after the fetch
    assert ctx.sh.calls == [["git", "-C", d, "fetch", "-q", "origin",
                             "+refs/tags/v0.6.0:refs/tags/v0.6.0"]]  # no prune, no worktree add


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


SHA = "0123456789abcdef0123456789abcdef01234567"


def test_tag_commit_peels_the_tag(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git", out=SHA + "\n")
    assert selfupdate.tag_commit(ctx.sh, "/i", "v0.6.1") == SHA
    assert ctx.sh.calls == [["git", "-C", "/i", "rev-parse", "--verify", "-q",
                             "refs/tags/v0.6.1^{commit}"]]


def test_self_update_refuses_a_different_commit(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git", out="f" * 40 + "\n").on(str(ctx.paths.bin_link))
    assert selfupdate.self_update(ctx, "v0.6.1", SHA) == 1
    assert capsys.readouterr().err == (f"self-update refused: v0.6.1 is {'f' * 12} here, the "
                                       f"hub installed {SHA[:12]}\n")
    assert not any("checkout" in c for c in ctx.sh.calls)
    assert not ctx.sh.called(str(ctx.paths.bin_link))


def test_self_update_with_the_matching_commit_proceeds(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git", out=SHA + "\n").on(str(ctx.paths.bin_link))
    assert selfupdate.self_update(ctx, "v0.6.1", SHA) == 0
    assert ["git", "-C", str(ctx.paths.install_dir), "checkout", "-q", "v0.6.1"] in ctx.sh.calls


def test_self_update_refuses_when_the_tag_cannot_be_resolved(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git", out="")
    assert selfupdate.self_update(ctx, "v0.6.1", SHA) == 1
    assert "self-update refused" in capsys.readouterr().err
    assert not any("checkout" in c for c in ctx.sh.calls)


def test_untrusted_dry_run_leaves_no_dry_run_dir(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git")

    def check(*a, **k):
        raise trust.Untrusted("bad sig")
    monkeypatch.setattr(trust, "check", check)
    with pytest.raises(trust.Untrusted):
        selfupdate.dry_run(ctx, "v0.6.0")
    assert not list(ctx.paths.state_dir.glob("dry-run-*"))

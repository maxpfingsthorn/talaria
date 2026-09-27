import os
import subprocess

from talaria import history, state
from talaria.shell import Shell
from tests.fakes import make_test_ctx


def gctx(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("git", fn=lambda argv, input: Shell().run(argv, check=False))
    d = ctx.conf.data_dir
    (d / "config.yaml").write_text("a: 1\n")
    (d / "memories").mkdir()
    (d / "memories/one.md").write_text("m1")
    (d / "memories/x.lock").write_text("l")
    os.symlink("/etc/passwd", d / "memories/evil.md")
    return ctx, state.load(ctx.paths)


def log(ctx):
    return subprocess.run(["git", "-C", ctx.paths.history, "log", "--format=%s"],
                          capture_output=True, text=True).stdout.split()


def tracked(ctx):
    return subprocess.run(["git", "-C", ctx.paths.history, "ls-files"],
                          capture_output=True, text=True).stdout.split()


def test_commits_config_and_memories_only(tmp_path):
    ctx, st = gctx(tmp_path)
    history.commit(ctx, st, "daily")
    assert tracked(ctx) == ["config.yaml", "memories/one.md"]
    assert ctx.notify.sent == []


def test_noop_when_unchanged_and_tracks_deletions(tmp_path):
    ctx, st = gctx(tmp_path)
    history.commit(ctx, st, "first")
    history.commit(ctx, st, "second")
    assert log(ctx) == ["first"]
    (ctx.conf.data_dir / "memories/one.md").unlink()
    history.commit(ctx, st, "third")
    assert tracked(ctx) == ["config.yaml"]


def test_failure_reported_once(tmp_path):
    ctx, st = gctx(tmp_path)
    ctx.sh.on("git", "-C", str(ctx.paths.history), "add", rc=1, err="disk full")
    history.commit(ctx, st, "a")
    history.commit(ctx, st, "b")
    assert len(ctx.notify.sent) == 1 and "disk full" in ctx.notify.sent[0].text


def test_exact_git_commands(tmp_path):
    ctx, st = gctx(tmp_path)
    st["history_error"] = "old"
    history.commit(ctx, st, "daily")
    h = str(ctx.paths.history)
    assert [c[3:] for c in ctx.sh.calls] == [
        ["init", "-q"], ["add", "-A"], ["status", "--porcelain"],
        ["-c", "user.name=Talaria", "-c", "user.email=talaria@localhost", "commit", "-qm", "daily"]]
    assert all(c[:3] == ["git", "-C", h] for c in ctx.sh.calls)
    assert set(ctx.sh.timeouts) == {120}
    assert st["history_error"] is None
    ctx.sh.calls.clear()
    history.commit(ctx, st, "again")
    assert [c[3] for c in ctx.sh.calls] == ["add", "status"]


def test_memories_symlinked_dir_is_ignored(tmp_path):
    ctx, st = gctx(tmp_path)
    d = ctx.conf.data_dir
    import shutil
    shutil.rmtree(d / "memories")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere/x.md").write_text("x")
    os.symlink(tmp_path / "elsewhere", d / "memories")
    history.commit(ctx, st, "daily")
    assert tracked(ctx) == ["config.yaml"]


def test_new_error_reported_again(tmp_path):
    ctx, st = gctx(tmp_path)
    ctx.sh.on("git", "-C", str(ctx.paths.history), "add", rc=1, err="disk full")
    history.commit(ctx, st, "a")
    ctx.sh.on("git", "-C", str(ctx.paths.history), "add", rc=1, err="other")
    history.commit(ctx, st, "b")
    assert len(ctx.notify.sent) == 2
    assert ctx.notify.sent[0].text.startswith("History commit failed: git -C ")


# ---- review C1: never write through a symlink in the history repo ----

def test_symlink_planted_in_repo_is_replaced_not_followed(tmp_path):
    ctx, st = gctx(tmp_path)
    history.commit(ctx, st, "first")
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    target = ctx.paths.history / "memories/one.md"
    target.unlink()
    os.symlink(victim, target)
    (ctx.conf.data_dir / "memories/one.md").write_text("agent content")
    history.commit(ctx, st, "second")
    assert victim.read_text() == "precious"
    assert not target.is_symlink() and target.read_text() == "agent content"


def test_source_swapped_to_symlink_is_not_read(tmp_path, monkeypatch):
    ctx, st = gctx(tmp_path)
    secret = tmp_path / "secret"
    secret.write_text("SECRET")
    real = history._wanted

    def racy(data):
        out = real(data)
        (data / "config.yaml").unlink()
        os.symlink(secret, data / "config.yaml")     # swapped after the check
        return out

    monkeypatch.setattr(history, "_wanted", racy)
    history.commit(ctx, st, "x")
    copied = ctx.paths.history / "config.yaml"
    assert not copied.exists() or "SECRET" not in copied.read_text()

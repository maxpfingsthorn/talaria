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

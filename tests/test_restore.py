import os
import shutil

import pytest

from talaria import backup, restore
from talaria.shell import Shell
from tests.fakes import make_test_ctx


def real_tar(ctx):
    ctx.sh.on("tar", fn=lambda argv, input: Shell().run(argv, check=False))


def setup_ctx(tmp_path):
    ctx = make_test_ctx(tmp_path)
    real_tar(ctx)
    d = ctx.conf.data_dir
    (d / "config.yaml").write_text("old\n")
    (d / ".cache").mkdir()
    (d / ".cache/c").write_text("cache")
    b = backup.create(ctx, "pre-x", None)
    (d / "config.yaml").write_text("new\n")
    (d / "added").write_text("after backup")
    (d / ".cache/c2").write_text("cache2")
    return ctx, b


def check_end_state(ctx, b):
    d = ctx.conf.data_dir
    assert (d / "config.yaml").read_text() == "old\n"
    assert not (d / "added").exists()
    assert (d / ".cache/c2").read_text() == "cache2"   # excluded paths survive
    assert not any(p.name.startswith(d.name + ".") for p in d.parent.iterdir())


def test_restore(tmp_path):
    ctx, b = setup_ctx(tmp_path)
    restore.restore_data(ctx, b)
    check_end_state(ctx, b)


@pytest.mark.parametrize("crash_after", ["extract", "rename_old", "rename_new", "move_excluded"])
def test_restore_resumes_after_each_step(tmp_path, monkeypatch, crash_after):
    ctx, b = setup_ctx(tmp_path)
    real = getattr(restore, f"_step_{crash_after}")

    def crashing(*a, **k):
        real(*a, **k)
        raise KeyboardInterrupt("simulated crash")

    monkeypatch.setattr(restore, f"_step_{crash_after}", crashing)
    with pytest.raises(KeyboardInterrupt):
        restore.restore_data(ctx, b)
    monkeypatch.setattr(restore, f"_step_{crash_after}", real)
    restore.restore_data(ctx, b)
    check_end_state(ctx, b)


def test_partial_extract_is_redone(tmp_path):
    ctx, b = setup_ctx(tmp_path)
    d = ctx.conf.data_dir
    partial = d.with_name(f"{d.name}.restore-{b.id}")
    partial.mkdir()
    (partial / "junk").write_text("half")
    restore.restore_data(ctx, b)
    check_end_state(ctx, b)
    assert not (d / "junk").exists()


def test_damaged_backup_changes_nothing(tmp_path):
    ctx, b = setup_ctx(tmp_path)
    with open(b.path, "ab") as f:
        f.write(b"x")
    with pytest.raises(backup.BackupError):
        restore.restore_data(ctx, b)
    assert (ctx.conf.data_dir / "config.yaml").read_text() == "new\n"


# ---- _move_missing (mutation testing) ----

def test_move_missing_cases(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "a/b").mkdir(parents=True)
    (src / "a/b/new.txt").write_text("n")
    (src / "a/keep.txt").write_text("src version")
    (src / "file_vs_dir").write_text("f")
    (src / "linkdir").symlink_to(src / "a")
    (dst / "a").mkdir(parents=True)
    (dst / "a/keep.txt").write_text("dst version")
    (dst / "file_vs_dir").mkdir()
    (dst / "linkdir").mkdir()
    restore._move_missing(src / "a", dst / "a")
    restore._move_missing(src / "file_vs_dir", dst / "file_vs_dir")
    restore._move_missing(src / "linkdir", dst / "linkdir")
    restore._move_missing(src / "missing", dst / "missing")
    (src / "fresh").mkdir()
    (src / "fresh/f.txt").write_text("f")
    restore._move_missing(src / "fresh", dst / "deep/er/fresh")
    assert (dst / "a/keep.txt").read_text() == "dst version"
    assert (dst / "a/b/new.txt").read_text() == "n"
    assert (dst / "file_vs_dir").is_dir() and (src / "file_vs_dir").exists()
    assert not (dst / "linkdir/b").exists()
    assert not (dst / "missing").exists()
    assert (dst / "deep/er/fresh/f.txt").read_text() == "f"


def test_move_missing_dangling_symlink_is_moved(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "dangling").symlink_to(tmp_path / "nowhere")
    restore._move_missing(src / "dangling", dst / "dangling")
    assert (dst / "dangling").is_symlink()


def test_restore_extract_exact_and_private(tmp_path, monkeypatch):
    ctx, b = setup_ctx(tmp_path)
    ctx.conf.data_dir.chmod(0o750)
    b = backup.create(ctx, "pre-y", None)        # tar restores the mode of "." from the archive
    modes = []
    real = restore._step_rename_old

    def spy(c, data, old):
        new = data.with_name(f"{data.name}.restore-{b.id}")
        modes.append(oct(new.stat().st_mode & 0o777))
        return real(c, data, old)

    monkeypatch.setattr(restore, "_step_rename_old", spy)
    restore.restore_data(ctx, b)
    assert modes == ["0o750"]
    tar = [c for c in ctx.sh.calls if c[0] == "tar"][0]
    d = ctx.conf.data_dir
    assert tar == ["tar", "-xzf", str(b.path), "-C", str(d.with_name(f"{d.name}.restore-{b.id}"))]
    assert ctx.sh.timeouts[ctx.sh.calls.index(tar)] == 3600
    assert not (d / restore.DONE).exists()


# ---- review I2: a leftover .old-<id> must not turn a restore into a no-op ----

def test_stale_old_dir_does_not_skip_restore(tmp_path):
    ctx, b = setup_ctx(tmp_path)
    d = ctx.conf.data_dir
    old = d.with_name(f"{d.name}.old-{b.id}")
    old.mkdir()
    (old / "leftover").write_text("from an earlier restore")
    restore.restore_data(ctx, b)
    check_end_state(ctx, b)


def test_undeletable_leftover_refuses_before_changing_anything(tmp_path, monkeypatch):
    ctx, b = setup_ctx(tmp_path)
    d = ctx.conf.data_dir
    old = d.with_name(f"{d.name}.old-{b.id}")
    old.mkdir()
    monkeypatch.setattr(restore.shutil, "rmtree", lambda p, ignore_errors=False: None)
    with pytest.raises(restore.RestoreError) as e:
        restore.restore_data(ctx, b)
    assert str(e.value) == f"cannot remove the leftover directory {old}"
    assert (d / "config.yaml").read_text() == "new\n"


def test_undeletable_leftover_after_success_blocks_the_next_restore(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root deletes everything")
    ctx, b = setup_ctx(tmp_path)
    d = ctx.conf.data_dir
    (d / "ro").mkdir()
    (d / "ro/f").write_text("x")
    (d / "ro").chmod(0o500)                 # the old copy of this cannot be deleted
    old = d.with_name(f"{d.name}.old-{b.id}")
    try:
        restore.restore_data(ctx, b)        # succeeds; the leftover stays
        assert (d / "config.yaml").read_text() == "old\n" and old.exists()
        (d / "config.yaml").write_text("changed again\n")
        with pytest.raises(restore.RestoreError):
            restore.restore_data(ctx, b)
        assert (d / "config.yaml").read_text() == "changed again\n"
    finally:
        for p in (old / "ro", d / "ro"):
            if p.exists():
                p.chmod(0o700)

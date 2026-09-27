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

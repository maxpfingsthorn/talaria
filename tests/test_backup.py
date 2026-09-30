import json
import os
import socket
import tarfile

import pytest

from talaria import backup, disk
from tests.fakes import make_test_ctx


def seed(data):
    (data / "config.yaml").write_text("_config_version: 27\n")
    (data / "memories").mkdir()
    (data / "memories/a.md").write_text("m")
    (data / ".cache").mkdir()
    (data / ".cache/big").write_text("x" * 1000)
    (data / "backups/config").mkdir(parents=True)
    (data / "backups/config/config.yaml.good.1").write_text("g")
    (data / "backups/other").write_text("o")
    os.symlink("/etc/passwd", data / "link")


def names(b):
    with tarfile.open(b.path) as t:
        return {m.name for m in t.getmembers()}


@pytest.mark.parametrize("rel,ex", [
    (".cache", True), (".cache/x", True), ("home/.npm/y", True), ("backups", False),
    ("backups/other", True), ("backups/config", False), ("backups/config/f", False),
    ("memories/a.md", False), (".cachet", False),
])
def test_excluded(rel, ex):
    assert backup.excluded(rel, (".cache", ".npm", "home/.cache", "home/.npm", "backups")) is ex


def test_create_writes_archive_and_sidecar(tmp_path):
    ctx = make_test_ctx(tmp_path)
    seed(ctx.conf.data_dir)
    b = backup.create(ctx, "pre-v2026.8.3", {"id": "sha256:i"})
    assert b.id == "20260927T043000Z-pre-v2026.8.3"
    n = names(b)
    assert "./config.yaml" in n and "./memories/a.md" in n
    assert "./backups/config/config.yaml.good.1" in n
    assert "./.cache/big" not in n and "./backups/other" not in n
    assert b.meta["cfg_version"] == 27 and b.meta["image"] == {"id": "sha256:i"}
    assert b.meta["label"] == "pre-v2026.8.3" and b.meta["data_size"] > 0
    backup.verify(b)
    assert backup.get(ctx, b.id).meta == b.meta


def test_backup_keeps_symlink_as_link(tmp_path):
    ctx = make_test_ctx(tmp_path)
    seed(ctx.conf.data_dir)
    b = backup.create(ctx, "manual", None)
    with tarfile.open(b.path) as t:
        m = t.getmember("./link")
    assert m.issym() and m.linkname == "/etc/passwd"


def test_backup_skips_socket(tmp_path):
    ctx = make_test_ctx(tmp_path)
    short = tmp_path / "s"   # AF_UNIX paths must be short
    short.mkdir()
    ctx.conf.data_dir = short
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(short / "sock"))
    try:
        b = backup.create(ctx, "manual", None)
    finally:
        s.close()
    assert "./sock" not in names(b)


def test_unreadable_file_fails_without_leftovers(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    ctx = make_test_ctx(tmp_path)
    f = ctx.conf.data_dir / "secret"
    f.write_text("x")
    f.chmod(0)
    with pytest.raises(PermissionError):
        backup.create(ctx, "manual", None)
    assert list(ctx.paths.backups.iterdir()) == []


def test_same_second_ids_do_not_collide(tmp_path):
    ctx = make_test_ctx(tmp_path)
    a = backup.create(ctx, "manual", None)
    b = backup.create(ctx, "manual", None)
    assert a.id != b.id


def test_verify_detects_corruption(tmp_path):
    ctx = make_test_ctx(tmp_path)
    b = backup.create(ctx, "manual", None)
    with open(b.path, "ab") as f:
        f.write(b"junk")
    with pytest.raises(backup.BackupError):
        backup.verify(b)


def test_archive_without_sidecar_is_not_a_backup(tmp_path):
    ctx = make_test_ctx(tmp_path)
    b = backup.create(ctx, "manual", None)
    (ctx.paths.backups / f"{b.id}.json").unlink()
    assert backup.list_backups(ctx) == []


def test_get_rejects_bad_ids(tmp_path):
    ctx = make_test_ctx(tmp_path)
    for bad in ["../x", "20260927T043000Z-../../etc", "nope"]:
        with pytest.raises(KeyError):
            backup.get(ctx, bad)


def test_prune_keeps_newest_and_protected(tmp_path):
    ctx = make_test_ctx(tmp_path, backup_keep=2)
    ids = []
    for i in range(4):
        ids.append(backup.create(ctx, f"b{i}", None).id)
        ctx.clock.sleep(1)
    removed = backup.prune(ctx, protect={ids[0]})
    left = [b.id for b in backup.list_backups(ctx)]
    assert left == [ids[3], ids[2], ids[0]] and removed == [ids[1]]


def test_ensure_space(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path, disk_floor_gb=1)
    monkeypatch.setattr(disk, "free_bytes", lambda p: 3 * disk.GB)
    disk.ensure_space(ctx, 2 * disk.GB, tmp_path)
    with pytest.raises(disk.NoSpace, match="GB"):
        disk.ensure_space(ctx, int(2.5 * disk.GB), tmp_path)


def test_third_backup_in_same_second(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ids = [backup.create(ctx, "manual", None).id for _ in range(3)]
    assert ids == ["20260927T043000Z-manual", "20260927T043000Z-manual-2",
                   "20260927T043000Z-manual-3"]


def test_orphan_sidecar_still_bumps_the_id(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.paths.backups.mkdir(parents=True)
    (ctx.paths.backups / "20260927T043000Z-manual.json").write_text("{}")
    assert backup.create(ctx, "manual", None).id == "20260927T043000Z-manual-2"


def test_bad_label_rejected_and_nothing_written(tmp_path):
    ctx = make_test_ctx(tmp_path)
    with pytest.raises(backup.BackupError) as e:
        backup.create(ctx, "Bad Label", None)
    assert str(e.value) == "bad backup label: 'Bad Label'"
    assert list(ctx.paths.backups.iterdir()) == []


def test_sidecar_exact(tmp_path):
    ctx = make_test_ctx(tmp_path)
    (ctx.conf.data_dir / "config.yaml").write_text("_config_version: 5\n")
    b = backup.create(ctx, "manual", {"tag": "t"})
    import hashlib
    assert b.meta == {"id": b.id, "label": "manual", "created": "2026-09-27T04:30:00+00:00",
                      "image": {"tag": "t"}, "cfg_version": 5,
                      "sha256": hashlib.sha256(b.path.read_bytes()).hexdigest(),
                      "size": b.path.stat().st_size, "data_size": 19}
    assert json.loads((ctx.paths.backups / f"{b.id}.json").read_text()) == b.meta
    assert (ctx.paths.backups.stat().st_mode & 0o777) == 0o700


def test_list_ignores_bad_sidecars(tmp_path):
    ctx = make_test_ctx(tmp_path)
    b = backup.create(ctx, "manual", None)
    (ctx.paths.backups / "junk.json").write_text("{}")
    (ctx.paths.backups / "20260927T043000Z-x.json").write_text("not json")
    (ctx.paths.backups / "20260927T043000Z-x.tar.gz").write_text("")
    assert [x.id for x in backup.list_backups(ctx)] == [b.id]


def test_prune_removes_stray_tmp(tmp_path):
    ctx = make_test_ctx(tmp_path)
    backup.create(ctx, "manual", None)
    (ctx.paths.backups / ".x.tar.gz.tmp").write_text("partial")
    backup.prune(ctx, set())
    assert not (ctx.paths.backups / ".x.tar.gz.tmp").exists()


# ---- review I7 ----

def test_renamable(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    assert disk.renamable(d) is True
    assert disk.renamable(tmp_path / "missing") is True
    monkeypatch.setattr(disk.os.path, "ismount", lambda p: str(p) == str(d))
    assert disk.renamable(d) is False


# ---- v0.2.4: hardlinks ----

def _extract(b, dest):
    import subprocess
    dest.mkdir()
    subprocess.run(["tar", "-xzf", str(b.path), "-C", str(dest)], check=True)


def test_backup_keeps_hardlinked_files(tmp_path):
    ctx = make_test_ctx(tmp_path)
    seed(ctx.conf.data_dir)
    (ctx.conf.data_dir / "b").mkdir()
    os.link(ctx.conf.data_dir / "memories/a.md", ctx.conf.data_dir / "b/g")
    b = backup.create(ctx, "manual", None)
    assert {"./memories/a.md", "./b/g"} <= names(b)
    _extract(b, tmp_path / "out")
    assert (tmp_path / "out/b/g").read_text() == "m"
    assert (tmp_path / "out/memories/a.md").read_text() == "m"


def test_hardlink_to_excluded_file_is_stored_as_a_file(tmp_path):
    ctx = make_test_ctx(tmp_path)
    seed(ctx.conf.data_dir)
    ctx.conf.backup_exclude = [*ctx.conf.backup_exclude, "memories/a.md"]
    os.link(ctx.conf.data_dir / "memories/a.md", ctx.conf.data_dir / "memories/b.md")
    b = backup.create(ctx, "manual", None)
    with tarfile.open(b.path) as t:
        m = t.getmember("./memories/b.md")
    assert m.isreg() and m.size == 1 and m.linkname == ""
    assert "./memories/a.md" not in names(b)
    _extract(b, tmp_path / "out")
    assert (tmp_path / "out/memories/b.md").read_text() == "m"


def test_hardlink_to_kept_file_stays_a_link(tmp_path):
    ctx = make_test_ctx(tmp_path)
    seed(ctx.conf.data_dir)
    os.link(ctx.conf.data_dir / "memories/a.md", ctx.conf.data_dir / "memories/b.md")
    b = backup.create(ctx, "manual", None)
    with tarfile.open(b.path) as t:
        m = t.getmember("./memories/b.md")
    assert m.islnk() and m.linkname == "./memories/a.md"

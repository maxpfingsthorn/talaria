from talaria import backup, lock, marker, state, status
from tests.fakes import make_test_ctx


def sctx(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("systemctl", "--user", "is-active", out="active\n")
    st = state.load(ctx.paths)
    st["current"] = {"tag": "v2026.8.3", "id": "sha256:0123456789abcdef"}
    state.save(ctx.paths, st)
    return ctx


def test_status_basic(tmp_path):
    ctx = sctx(tmp_path)
    t = status.status_text(ctx)
    assert "Hermes v2026.8.3" in t and "running" in t and "GB free" in t


def test_status_pending_and_talaria_update(tmp_path):
    ctx = sctx(tmp_path)
    st = state.load(ctx.paths)
    st["pending"] = {"tag": "v2026.9.24"}
    st["talaria_notified"] = "v99.0.0"
    state.save(ctx.paths, st)
    t = status.status_text(ctx)
    assert "/approve v2026.9.24" in t and "Talaria v99.0.0 is available" in t


def test_interrupted_with_marker(tmp_path):
    ctx = sctx(tmp_path)
    st = state.load(ctx.paths)
    st["op"] = {"op": "deploy", "tag": "v2026.9.24", "backup": "b", "started": ctx.now().isoformat()}
    marker.write(ctx.paths, "deploy", "b", {}, ctx.now())
    assert "Hermes is stopped" in status.interrupted_text(ctx, st)


def test_interrupted_without_marker(tmp_path):
    ctx = sctx(tmp_path)
    st = state.load(ctx.paths)
    st["op"] = {"op": "deploy", "tag": "v2026.9.24", "backup": "b", "started": ctx.now().isoformat()}
    assert "not verified" in status.interrupted_text(ctx, st)


def test_running_operation_is_not_interrupted(tmp_path):
    ctx = sctx(tmp_path)
    st = state.load(ctx.paths)
    st["op"] = {"op": "deploy", "tag": "v2026.9.24", "backup": "b", "started": ctx.now().isoformat()}
    with lock.op_lock(ctx.paths):
        assert "in progress" in status.interrupted_text(ctx, st)


def test_backups_text(tmp_path):
    ctx = sctx(tmp_path)
    assert status.backups_text(ctx) == "No backups yet."
    b = backup.create(ctx, "manual", {"tag": "v2026.8.3"})
    t = status.backups_text(ctx)
    assert b.id in t and "manual" in t and "v2026.8.3" in t


# ---- exact behaviour (mutation testing) ----

from talaria import disk


def test_status_exact(tmp_path, monkeypatch):
    ctx = sctx(tmp_path)
    monkeypatch.setattr(disk, "free_bytes", lambda p: int(12.34 * disk.GB))
    st = state.load(ctx.paths)
    st["pending"] = {"tag": "v2026.9.24"}
    st["talaria_notified"] = "v99.0.0"
    st["op"] = {"op": "deploy", "tag": "v2026.9.24", "started": ctx.now().isoformat()}
    state.save(ctx.paths, st)
    marker.write(ctx.paths, "deploy", "b", {}, ctx.now())
    ctx.clock.sleep(7200)
    from talaria import __version__
    assert status.status_text(ctx) == "\n".join([
        "Hermes v2026.8.3 (0123456789ab), running.",
        "12.3 GB free.",
        "Pending: v2026.9.24. /approve v2026.9.24 · /reject v2026.9.24",
        "Interrupted deploy v2026.9.24 (2h ago). Hermes is stopped. Send /rollback CONFIRM to "
        "restore the state before it.",
        f"Talaria v99.0.0 is available (installed v{__version__})."])


def test_status_minimal(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("systemctl", "--user", "is-active", out="inactive\n")
    monkeypatch.setattr(disk, "free_bytes", lambda p: 0)
    st = state.load(ctx.paths)
    st["talaria_notified"] = "v0.0.1"
    state.save(ctx.paths, st)
    assert status.status_text(ctx) == "Hermes unknown (), not running.\n0.0 GB free."


def test_interrupted_texts_exact(tmp_path):
    ctx = sctx(tmp_path)
    st = {"op": {"op": "restore", "backup": "B1", "started": ctx.now().isoformat()}}
    ctx.clock.sleep(120)
    assert status.interrupted_text(ctx, st) == (
        "Interrupted restore B1 (2m ago): the new version is running but was not verified. "
        "/rollback CONFIRM goes back.")
    with lock.op_lock(ctx.paths):
        assert status.interrupted_text(ctx, st) == "An operation is in progress: restore B1."
    st = {"op": {"op": "rollback", "started": ctx.now().isoformat()}}
    with lock.op_lock(ctx.paths):
        assert status.interrupted_text(ctx, st) == "An operation is in progress: rollback."
    assert status.interrupted_text(ctx, {"op": None}) is None
    assert status.interrupted_text(ctx, {}) is None


def test_backups_text_exact(tmp_path):
    ctx = sctx(tmp_path)
    b1 = backup.create(ctx, "manual", {"tag": "v2026.8.3"})
    ctx.clock.sleep(3 * 86400)
    b2 = backup.create(ctx, "adopt", None)
    size1 = b1.meta["size"] / disk.GB
    assert status.backups_text(ctx) == (
        f"{b2.id}  adopt  0m old  {b2.meta['size'] / disk.GB:.2f} GB  Hermes None\n"
        f"{b1.id}  manual  3d old  {size1:.2f} GB  Hermes v2026.8.3")

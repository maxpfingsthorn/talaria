from talaria import backup, lock, marker, state, status
from tests.fakes import make_test_ctx


def sctx(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("systemctl", "--user", "is-active", out="active\n")
    st = state.load(ctx.paths)
    st["current"] = {"tag": "v2026.8.3", "id": "sha256:abcdef0123456789"}
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

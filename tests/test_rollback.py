from talaria import backup, deploy, marker, rollback, state
from tests.opsfakes import CUR, NEW, load, ops_ctx


def deployed(tmp_path, monkeypatch, checks=None):
    ctx = ops_ctx(tmp_path, monkeypatch, check_results=checks)
    deploy.deploy(ctx, "v2026.9.24")
    (ctx.conf.data_dir / "memories/m.md").write_text("written after deploy")
    ctx.notify.sent.clear()
    return ctx


def test_describe_names_backup_age(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    ctx.clock.sleep(3 * 3600)
    text = rollback.describe(ctx)
    assert "3h" in text and "v2026.8.3" in text and "/rollback CONFIRM" in text


def test_rollback_after_deploy(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    rollback.rollback_cmd(ctx)
    st = load(ctx)
    assert st["current"] == CUR and st["last_deploy"] is None
    assert (ctx.conf.data_dir / "memories/m.md").read_text() == "before"
    assert "Rolled back to Hermes v2026.8.3" in ctx.notify.sent[-1].text


def test_rollback_twice_is_refused_cleanly(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    rollback.rollback_cmd(ctx)
    rollback.rollback_cmd(ctx)
    assert "Nothing to roll back" in ctx.notify.sent[-1].text
    assert load(ctx)["current"] == CUR


def test_rollback_after_interrupted_deploy_uses_marker(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    b = backup.create(ctx, "pre-v2026.9.24", CUR)
    st = load(ctx)
    st["op"] = {"op": "deploy", "tag": "v2026.9.24", "backup": b.id, "started": "x"}
    state.save(ctx.paths, st)
    marker.write(ctx.paths, "deploy", b.id, CUR, ctx.now())
    (ctx.conf.data_dir / "config.yaml").write_text("_config_version: 30\n")  # half-migrated
    rollback.rollback_cmd(ctx)
    assert (ctx.conf.data_dir / "config.yaml").read_text() == "_config_version: 27\n"
    assert marker.read(ctx.paths) is None and load(ctx)["op"] is None


def test_rollback_is_rerunnable_after_crash_mid_restore(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    from talaria import restore
    real = restore._step_rename_old

    def crash(*a):
        real(*a)
        raise KeyboardInterrupt

    monkeypatch.setattr(restore, "_step_rename_old", crash)
    try:
        rollback.rollback_cmd(ctx)
    except KeyboardInterrupt:
        pass
    assert marker.read(ctx.paths) is not None           # Hermes blocked from starting
    monkeypatch.setattr(restore, "_step_rename_old", real)
    rollback.rollback_cmd(ctx)
    assert load(ctx)["current"] == CUR and marker.read(ctx.paths) is None


def test_restore_cmd_and_undo(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    target = backup.get(ctx, load(ctx)["last_deploy"]["backup"])
    rollback.restore_cmd(ctx, target.id)
    st = load(ctx)
    assert st["current"] == CUR
    assert (ctx.conf.data_dir / "memories/m.md").read_text() == "before"
    pre = [b for b in backup.list_backups(ctx) if b.meta["label"] == "pre-restore"][0]
    assert f"/restore {pre.id} CONFIRM" in ctx.notify.sent[-1].commands


def test_restore_unknown_id(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    rollback.restore_cmd(ctx, "20200101T000000Z-nope")
    assert "No backup" in ctx.notify.sent[-1].text


def test_restore_failed_check_reverts_to_pre_restore(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    ctx.checks[:] = ["broken", None]
    target = load(ctx)["last_deploy"]["backup"]
    rollback.restore_cmd(ctx, target)
    assert load(ctx)["current"] == NEW
    assert (ctx.conf.data_dir / "memories/m.md").read_text() == "written after deploy"
    assert "reverted" in ctx.notify.sent[-1].text


def test_describe_restore_exact(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    b = backup.get(ctx, load(ctx)["last_deploy"]["backup"])
    ctx.clock.sleep(2 * 86400 + 5)
    assert rollback.describe_restore(ctx, b.id) == (
        f"Restore replaces all Hermes data with backup {b.id} (2d old, Hermes v2026.8.3). "
        "Talaria first takes a pre-restore backup, so this can be undone.\n"
        f"Send /restore {b.id} CONFIRM to proceed.")


def test_describe_restore_unknown():
    assert rollback.describe_restore(None, "nope") == "No backup nope. /backups lists them."


def test_describe_exact(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    b = load(ctx)["last_deploy"]["backup"]
    ctx.clock.sleep(90)
    assert rollback.describe(ctx) == (
        f"Rollback restores backup {b} (1m old) and Hermes v2026.8.3. Everything Hermes wrote "
        "since then is lost.\nSend /rollback CONFIRM to proceed.")


def test_describe_nothing(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    assert rollback.describe(ctx) == ("Nothing to roll back: no interrupted change and no "
                                      "previous deploy.")


def test_age_units(tmp_path):
    from tests.fakes import make_test_ctx
    ctx = make_test_ctx(tmp_path)
    t = ctx.now().isoformat()
    for secs, want in [(0, "0m"), (59, "0m"), (60, "1m"), (3599, "59m"), (3600, "1h"),
                       (86399, "23h"), (86400, "1d"), (3 * 86400 + 7200, "3d")]:
        ctx.clock.slept = secs
        assert rollback.age(ctx, t) == want

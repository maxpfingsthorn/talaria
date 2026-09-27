import pytest
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


# ---- exact behaviour (mutation testing) ----

from talaria import disk, images
from tests.opsfakes import ops_ctx as _ops


def spy_swap(monkeypatch, ctx):
    seen = []
    from talaria import restore
    real = rollback.restore_data

    def spy(c, b):
        seen.append({"marker": marker.read(ctx.paths), "op": load(ctx)["op"], "backup": b.id})
        return real(c, b)

    monkeypatch.setattr(rollback, "restore_data", spy)
    return seen


def test_user_rollback_marker_and_op(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    bid = load(ctx)["last_deploy"]["backup"]
    seen = spy_swap(monkeypatch, ctx)
    rollback.rollback_cmd(ctx)
    assert seen == [{"marker": {"op": "rollback", "backup": bid, "image": CUR,
                                "written": "2026-09-27T04:30:00+00:00"},
                     "op": {"op": "rollback", "backup": bid, "changed": True,
                            "started": "2026-09-27T04:30:00+00:00"},
                     "backup": bid}]
    st = load(ctx)
    assert (st["current"], st["previous"], st["last_deploy"], st["op"]) == (CUR, None, None, None)
    assert ctx.notify.sent[-1].text == "Rolled back to Hermes v2026.8.3."


def test_interrupted_deploy_keeps_its_op_and_marker_name(tmp_path, monkeypatch):
    ctx = _ops(tmp_path, monkeypatch)
    b = backup.create(ctx, "pre-v2026.9.24", CUR)
    st = load(ctx)
    st["op"] = {"op": "deploy", "tag": "v2026.9.24", "backup": b.id, "started": "x"}
    state.save(ctx.paths, st)
    marker.write(ctx.paths, "deploy", b.id, CUR, ctx.now())
    seen = spy_swap(monkeypatch, ctx)
    rollback.rollback_cmd(ctx)
    assert seen[0]["marker"]["op"] == "deploy" and seen[0]["op"]["op"] == "deploy"


def test_rollback_failure_keeps_op_name_in_marker(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    ctx.checks[:] = ["broken"]
    rollback.rollback_cmd(ctx)
    m = marker.read(ctx.paths)
    assert m["op"] == "rollback" and m["image"] == CUR
    assert ctx.notify.sent[-1].text == ("Rollback failed: broken. Hermes is stopped. "
                                        "Manual recovery: see README, section 'Manual recovery'.")


def test_rollback_exception_text_exact(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    monkeypatch.setattr(rollback, "restore_data", lambda c, b: (_ for _ in ()).throw(OSError("io")))
    rollback.rollback_cmd(ctx)
    assert ctx.notify.sent[-1].text == ("Rollback failed: io. Hermes may be stopped. "
                                        "Manual recovery: see README, section 'Manual recovery'.")


def test_rollback_nothing_text_exact(tmp_path, monkeypatch):
    ctx = _ops(tmp_path, monkeypatch)
    rollback.rollback_cmd(ctx)
    assert ctx.notify.sent[-1].text == ("Nothing to roll back: no interrupted change and no "
                                        "previous deploy.")


def test_rollback_needs_space_for_the_data(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    b = backup.get(ctx, load(ctx)["last_deploy"]["backup"])
    monkeypatch.setattr(disk, "free_bytes", lambda p: b.meta["data_size"] - 1)
    rollback.rollback_cmd(ctx)
    assert ctx.notify.sent[-1].text.startswith("Rollback failed: need ")
    assert load(ctx)["current"] == NEW


def test_target_needs_previous_too(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    st = load(ctx)
    st["previous"] = None
    assert rollback.target(ctx, st) is None


def test_restore_markers_op_and_texts(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    target = load(ctx)["last_deploy"]["backup"]
    seen = spy_swap(monkeypatch, ctx)
    rollback.restore_cmd(ctx, target)
    pre = [b for b in backup.list_backups(ctx) if b.meta["label"] == "pre-restore"][0]
    assert pre.meta["image"] == NEW
    assert seen[0]["marker"] == {"op": "restore", "backup": pre.id, "image": NEW,
                                 "written": "2026-09-27T04:30:00+00:00"}
    assert seen[0]["op"] == {"op": "restore", "backup": target, "changed": True,
                             "started": "2026-09-27T04:30:00+00:00"}
    m = ctx.notify.sent[-1]
    assert (m.text, m.commands) == (f"Restored backup {target} (Hermes v2026.8.3).",
                                    [f"/restore {pre.id} CONFIRM"])
    assert load(ctx)["op"] is None and marker.read(ctx.paths) is None


def test_restore_failure_texts_exact(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    target = load(ctx)["last_deploy"]["backup"]
    ctx.checks[:] = ["broken", None]
    rollback.restore_cmd(ctx, target)
    assert ctx.notify.sent[-1].text == (f"Restore of {target} failed: broken. Hermes reverted to "
                                        "the state before the restore.")
    ctx.checks[:] = ["broken", "still broken"]
    rollback.restore_cmd(ctx, target)
    assert ctx.notify.sent[-1].text == (
        f"Restore of {target} failed: broken. Reverting failed too: still broken. Hermes is "
        "stopped. Manual recovery: see README, section 'Manual recovery'.")
    assert marker.read(ctx.paths)["op"] == "restore"


def test_restore_refusals_exact(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    b = backup.create(ctx, "manual", None)
    rollback.restore_cmd(ctx, b.id)
    assert ctx.notify.sent[-1].text == (f"Restore of {b.id} refused: the backup records no image. "
                                        "Nothing was changed.")
    rollback.restore_cmd(ctx, "20200101T000000Z-x")
    assert ctx.notify.sent[-1].text == "No backup 20200101T000000Z-x. /backups lists them."


def test_restore_needs_space_for_two_copies(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    b = backup.get(ctx, load(ctx)["last_deploy"]["backup"])
    monkeypatch.setattr(disk, "free_bytes", lambda p: int(b.meta["data_size"] * 1.5))
    rollback.restore_cmd(ctx, b.id)
    assert "refused: need " in ctx.notify.sent[-1].text


# ---- review I3/I4/I5: every path that stops Hermes can be recovered ----

def test_deploy_records_op_before_stopping(tmp_path, monkeypatch):
    ctx = _ops(tmp_path, monkeypatch)
    seen = []
    real_stop = deploy.hermes.stop
    monkeypatch.setattr(deploy.hermes, "stop", lambda c: (seen.append(load(c)["op"]), real_stop(c))[1])
    deploy.deploy(ctx, "v2026.9.24")
    assert seen[0] == {"op": "deploy", "tag": "v2026.9.24", "backup": None, "changed": False,
                       "started": "2026-09-27T04:30:00+00:00"}


def test_crash_during_deploy_backup_is_recovered_by_starting(tmp_path, monkeypatch):
    ctx = _ops(tmp_path, monkeypatch)
    real = backup.create
    monkeypatch.setattr(backup, "create", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        deploy.deploy(ctx, "v2026.9.24")
    monkeypatch.setattr(backup, "create", real)
    assert marker.read(ctx.paths) is None and load(ctx)["op"]["changed"] is False
    assert "not running" in __import__("talaria.status", fromlist=["x"]).interrupted_text(ctx, load(ctx))
    ctx.sh.calls.clear()
    rollback.rollback_cmd(ctx)
    assert ctx.notify.sent[-1].text == ("Hermes v2026.8.3 is running again. The interrupted "
                                        "deploy had nothing left to undo.")
    assert ctx.sh.called("systemctl", "--user", "start", "hermes.service")
    st = load(ctx)
    assert st["op"] is None and st["current"] == CUR and st["pending"]["tag"] == "v2026.9.24"


def test_deploy_early_failure_clears_op(tmp_path, monkeypatch):
    ctx = _ops(tmp_path, monkeypatch)
    monkeypatch.setattr(backup, "create", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    deploy.deploy(ctx, "v2026.9.24")
    assert load(ctx)["op"] is None


def test_crash_after_rollback_swap_is_recovered(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    monkeypatch.setattr(rollback.hermes, "post_start_check",
                        lambda c: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        rollback.rollback_cmd(ctx)
    assert marker.read(ctx.paths) is None and load(ctx)["op"]["op"] == "rollback"
    monkeypatch.setattr(rollback.hermes, "post_start_check", lambda c: None)
    assert "/rollback CONFIRM" in rollback.describe(ctx)
    rollback.rollback_cmd(ctx)
    assert ctx.notify.sent[-1].text == ("Hermes v2026.8.3 is running again. The interrupted "
                                        "rollback had nothing left to undo.")
    assert load(ctx)["op"] is None


def test_resume_failure_is_reported(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    st = load(ctx)
    st["op"] = {"op": "restore", "backup": "b", "changed": True, "started": "x"}
    state.save(ctx.paths, st)
    ctx.checks[:] = ["still down"]
    rollback.rollback_cmd(ctx)
    assert ctx.notify.sent[-1].text == ("Recovery failed: still down. Hermes is stopped. "
                                        "Manual recovery: see README, section 'Manual recovery'.")
    assert load(ctx)["op"]["op"] == "restore"


def test_restore_pre_backup_failure_restarts_hermes(tmp_path, monkeypatch):
    ctx = deployed(tmp_path, monkeypatch)
    target = load(ctx)["last_deploy"]["backup"]
    monkeypatch.setattr(backup, "create", lambda *a: (_ for _ in ()).throw(OSError("unreadable")))
    rollback.restore_cmd(ctx, target)
    assert ctx.notify.sent[-1].text == (f"Restore of {target} failed before changing anything: "
                                        "unreadable. Hermes was started again.")
    assert ctx.sh.calls[-1] == ["systemctl", "--user", "start", "hermes.service"]
    assert load(ctx)["op"] is None and load(ctx)["current"] == NEW


def test_interrupted_text_reports_real_service_state(tmp_path, monkeypatch):
    from talaria import status
    ctx = deployed(tmp_path, monkeypatch)
    st = load(ctx)
    st["op"] = {"op": "deploy", "tag": "v2026.9.24", "backup": "b", "changed": True,
                "started": ctx.now().isoformat()}
    ctx.sh.on("systemctl", "--user", "is-active", out="active\n")
    assert status.interrupted_text(ctx, st) == (
        "Interrupted deploy v2026.9.24 (0m ago): Hermes is running but the change was not "
        "verified. Send /rollback CONFIRM to recover.")
    ctx.sh.on("systemctl", "--user", "is-active", out="inactive\n")
    assert "Hermes is not running but" in status.interrupted_text(ctx, st)

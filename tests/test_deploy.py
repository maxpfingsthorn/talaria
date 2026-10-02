import subprocess
from talaria import backup, deploy, marker
from talaria.apps import hermes as hermes_app
from tests.opsfakes import CUR, NEW, load, ops_ctx


def test_deploy_happy_path(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    deploy.deploy(ctx, "v2026.9.24")
    st = load(ctx)
    assert st["current"] == NEW and st["previous"] == CUR and st["pending"] is None
    assert st["op"] is None and marker.read(ctx.paths) is None
    assert st["last_deploy"]["tag"] == "v2026.9.24"
    assert backup.get(ctx, st["last_deploy"]["backup"]).meta["image"] == CUR
    assert ctx.sh.called("systemctl", "--user", "stop", "hermes.service")
    assert ["podman", "tag", "sha256:new", "localhost/hermes-agent:current"] in ctx.sh.calls
    assert "Deployed Hermes v2026.9.24" in ctx.notify.sent[-1].text
    assert (ctx.conf.data_dir / "config.yaml").read_text() == "_config_version: 30\n"


def test_marker_removed_before_start(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    seen = []
    orig_start = deploy.service.start
    monkeypatch.setattr(deploy.service, "start",
                        lambda c: (seen.append(marker.read(c.paths)), orig_start(c)))
    deploy.deploy(ctx, "v2026.9.24")
    assert seen == [None]


def test_deploy_only_pending_tag(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    deploy.deploy(ctx, "v2026.9.7")
    assert "not the pending candidate" in ctx.notify.sent[-1].text
    assert not ctx.sh.called("systemctl", "--user", "stop")


def test_deploy_refused_without_space(tmp_path, monkeypatch):
    from talaria import disk
    ctx = ops_ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(disk, "free_bytes", lambda p: 0)
    deploy.deploy(ctx, "v2026.9.24")
    assert "Nothing was stopped" in ctx.notify.sent[-1].text
    assert not ctx.sh.called("systemctl", "--user", "stop")


def test_migration_failure_rolls_back(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch, migrate={"ok": False, "before": 27, "after": 27,
                                                   "latest": 30, "messages": ["✗"],
                                                   "error": "boom"})
    deploy.deploy(ctx, "v2026.9.24")
    st = load(ctx)
    assert st["current"] == CUR and "v2026.9.24" in st["failed"] and st["pending"] is None
    assert marker.read(ctx.paths) is None and st["op"] is None
    assert "boom" in ctx.notify.sent[-1].text and "Rolled back" in ctx.notify.sent[-1].text
    assert (ctx.conf.data_dir / "config.yaml").read_text() == "_config_version: 27\n"


def test_unexpected_config_version_rolls_back(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch, migrate={"ok": True, "before": 27, "after": 29,
                                                   "latest": 29, "messages": [], "error": None})
    deploy.deploy(ctx, "v2026.9.24")
    assert "expected 30" in ctx.notify.sent[-1].text and load(ctx)["current"] == CUR


def test_failed_post_start_check_rolls_back_to_previous(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch, check_results=["hermes.service restarted", None])
    (ctx.conf.data_dir / "memories/m.md").write_text("before")
    deploy.deploy(ctx, "v2026.9.24")
    st = load(ctx)
    assert st["current"] == CUR and "v2026.9.24" in st["failed"]
    assert ["podman", "tag", "sha256:cur", "localhost/hermes-agent:current"] == \
        ctx.sh.called("podman", "tag")[-1]
    assert "restarted" in ctx.notify.sent[-1].text
    assert len(ctx.notify.sent) == 1


def test_rollback_failure_leaves_marker_and_says_so(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch, check_results=["restarted", "still broken"])
    deploy.deploy(ctx, "v2026.9.24")
    assert marker.read(ctx.paths) is not None
    assert "Hermes is stopped" in ctx.notify.sent[-1].text
    assert ctx.sh.called("systemctl", "--user", "stop")[-1][-1] == "hermes.service"


def test_retention_after_deploy(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    ctx.conf.backup_keep = 1
    for i in range(3):
        backup.create(ctx, f"manual", CUR)
        ctx.clock.sleep(1)
    deploy.deploy(ctx, "v2026.9.24")
    ids = [b.id for b in backup.list_backups(ctx)]
    assert load(ctx)["last_deploy"]["backup"] in ids and len(ids) == 1


# ---- exact behaviour (mutation testing) ----

import pytest

from talaria import state
from tests.opsfakes import load


class Killed(Exception):
    pass


def test_crash_hook_kills_after_marker(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    monkeypatch.setenv("TALARIA_TEST_CRASH_AT", "after_marker")
    seen = []
    monkeypatch.setattr(deploy.os, "kill", lambda pid, sig: (seen.append((pid, sig)), (_ for _ in ()).throw(Killed()))[1])
    with pytest.raises(Killed):
        deploy.deploy(ctx, "v2026.9.24")
    import os
    import signal
    assert seen == [(os.getpid(), signal.SIGKILL)]
    assert marker.read(ctx.paths)["op"] == "deploy"


def test_crash_hook_other_name_does_nothing(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    monkeypatch.setenv("TALARIA_TEST_CRASH_AT", "elsewhere")
    deploy.deploy(ctx, "v2026.9.24")
    assert "Deployed" in ctx.notify.sent[-1].text


def test_state_and_marker_during_migration(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    seen = {}

    def spy(c, rec, script, data, work, args=()):
        seen.update(ctx_ok=c is ctx, rec=rec, script=script, data=data, work=work,
                    op=load(ctx)["op"], marker=marker.read(ctx.paths))
        (data / "config.yaml").write_text("_config_version: 30\n")
        return {"ok": True, "before": 27, "after": 30, "latest": 30, "messages": [], "error": None}

    monkeypatch.setattr(hermes_app, "run_helper", spy)
    deploy.deploy(ctx, "v2026.9.24")
    bid = load(ctx)["last_deploy"]["backup"]
    assert seen["ctx_ok"] and seen["rec"] == NEW and seen["script"] == "migrate.py"
    assert seen["data"] == ctx.conf.data_dir and seen["work"] == ctx.paths.staging
    assert seen["op"] == {"op": "deploy", "tag": "v2026.9.24", "backup": bid, "changed": True,
                          "started": "2026-09-27T04:30:00+00:00"}
    assert seen["marker"] == {"op": "deploy", "backup": bid, "image": CUR,
                              "written": "2026-09-27T04:30:00+00:00"}


def test_deploy_success_exact(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    st = load(ctx)
    st["adopt_backup"] = "x"
    state.save(ctx.paths, st)
    deploy.deploy(ctx, "v2026.9.24")
    m = ctx.notify.sent[-1]
    assert (m.text, m.commands) == ("Deployed Hermes v2026.9.24.", ["/rollback"])
    assert load(ctx)["adopt_backup"] is None
    tags = ctx.sh.called("podman", "tag")
    assert tags == [["podman", "tag", "sha256:cur", "localhost/hermes-agent:previous"],
                    ["podman", "tag", "sha256:new", "localhost/hermes-agent:current"]]
    log = subprocess.run(["git", "-C", ctx.paths.history, "log", "--format=%s"],
                         capture_output=True, text=True).stdout
    assert log == "before deploy v2026.9.24\n"


def test_refusal_messages_exact(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    deploy.deploy(ctx, "v2026.9.7")
    assert ctx.notify.sent[-1].text == "Nothing deployed: v2026.9.7 is not the pending candidate."


def test_space_needs_twice_the_data(tmp_path, monkeypatch):
    from talaria import disk
    ctx = ops_ctx(tmp_path, monkeypatch)
    size = disk.dir_size(ctx.conf.data_dir)
    monkeypatch.setattr(disk, "free_bytes", lambda p: int(size * 1.5))
    deploy.deploy(ctx, "v2026.9.24")
    assert ctx.notify.sent[-1].text.startswith("Deploy of v2026.9.24 refused: need ")
    assert ctx.notify.sent[-1].text.endswith(". Nothing was stopped.")


def test_missing_image_refused(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    st = load(ctx)
    st["pending"]["image"]["ref"] = None
    state.save(ctx.paths, st)
    ctx.sh.on("podman", "image", "exists", rc=1)
    deploy.deploy(ctx, "v2026.9.24")
    assert "refused" in ctx.notify.sent[-1].text and not ctx.sh.called("systemctl", "--user", "stop")


def test_backup_failure_restarts_hermes_exact(tmp_path, monkeypatch):
    from talaria import backup
    ctx = ops_ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(backup, "create", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    deploy.deploy(ctx, "v2026.9.24")
    assert ctx.notify.sent[-1].text == ("Deploy of v2026.9.24 failed before changing anything: "
                                        "disk full. Hermes was started again.")
    assert ctx.sh.calls[-1] == ["systemctl", "--user", "start", "hermes.service"]
    assert marker.read(ctx.paths) is None and load(ctx)["op"] is None


def test_helper_crash_rolls_back_exact(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(hermes_app, "run_helper",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no result")))
    deploy.deploy(ctx, "v2026.9.24")
    m = ctx.notify.sent[-1]
    assert m.text == ("Hermes v2026.9.24 failed during deploy: migration could not run: no result. "
                      "Rolled back to Hermes v2026.8.3.")
    assert m.untrusted == [] and load(ctx)["failed"] == ["v2026.9.24"]


def test_migration_failure_message_and_details_exact(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch, migrate={"ok": False, "before": 27, "after": 27,
                                                   "latest": 30, "messages": ["✗ a", "✗ b"],
                                                   "error": "boom"})
    deploy.deploy(ctx, "v2026.9.24")
    m = ctx.notify.sent[-1]
    assert m.text == ("Hermes v2026.9.24 failed during deploy: migration failed: boom. "
                      "Rolled back to Hermes v2026.8.3.")
    assert m.untrusted == ["✗ a", "✗ b"]


def test_version_mismatch_message_exact(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch, migrate={"ok": True, "before": 27, "after": 29,
                                                   "latest": 29, "messages": ["m"], "error": None})
    deploy.deploy(ctx, "v2026.9.24")
    m = ctx.notify.sent[-1]
    assert m.text == ("Hermes v2026.9.24 failed during deploy: config version 29, expected 30 "
                      "from the rehearsal. Rolled back to Hermes v2026.8.3.")
    assert m.untrusted == ["m"]


def test_start_exception_is_a_failure(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch, check_results=[None])
    calls = []

    def start(c):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("unit missing")

    monkeypatch.setattr(deploy.service, "start", start)
    deploy.deploy(ctx, "v2026.9.24")
    assert ctx.notify.sent[-1].text.startswith(
        "Hermes v2026.9.24 failed during deploy: unit missing. Rolled back")


def test_rollback_exception_text_exact(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch, check_results=["restarted"])
    monkeypatch.setattr(deploy, "rollback", lambda c: (_ for _ in ()).throw(OSError("tar broke")))
    deploy.deploy(ctx, "v2026.9.24")
    assert ctx.notify.sent[-1].text == (
        "Hermes v2026.9.24 failed during deploy: restarted. Rollback also failed: tar broke. "
        "Hermes is stopped. Manual recovery: see README, section 'Manual recovery'.")
    assert marker.read(ctx.paths)["op"] == "deploy"


def test_failed_tag_recorded_once(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch, check_results=["restarted", None])
    st = load(ctx)
    st["failed"] = ["v2026.9.24"]
    state.save(ctx.paths, st)
    deploy.deploy(ctx, "v2026.9.24")
    assert load(ctx)["failed"] == ["v2026.9.24"]


def test_space_twice_is_enough(tmp_path, monkeypatch):
    from talaria import disk
    ctx = ops_ctx(tmp_path, monkeypatch)
    size = disk.dir_size(ctx.conf.data_dir)
    monkeypatch.setattr(disk, "free_bytes", lambda p: int(size * 2.5))
    deploy.deploy(ctx, "v2026.9.24")
    assert ctx.notify.sent[-1].text == "Deployed Hermes v2026.9.24."


# ---- v0.2.5: nothing new starts on an interrupted change ----

def test_deploy_refused_while_marker_exists(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    b = backup.create(ctx, "pre-v2026.9.24", CUR)
    marker.write(ctx.paths, "deploy", b.id, CUR, ctx.now())
    deploy.deploy(ctx, "v2026.9.24")
    assert ctx.notify.sent[-1].text == (
        "Deploy of v2026.9.24 refused: an interrupted deploy must be recovered first: send "
        "/rollback CONFIRM. Nothing was changed.")
    assert not ctx.sh.called("systemctl", "--user", "stop")
    assert [x.id for x in backup.list_backups(ctx)] == [b.id]


def test_deploy_refused_while_op_is_recorded(tmp_path, monkeypatch):
    from talaria import state
    ctx = ops_ctx(tmp_path, monkeypatch)
    st = load(ctx)
    st["op"] = {"op": "deploy", "tag": "v2026.9.24", "backup": "b", "changed": True,
                "started": "x"}
    state.save(ctx.paths, st)
    deploy.deploy(ctx, "v2026.9.24")
    assert "refused: an interrupted deploy" in ctx.notify.sent[-1].text
    assert not ctx.sh.called("systemctl", "--user", "stop")


def test_deploy_runs_the_apps_hooks_in_order(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    order = []
    real_before = type(ctx.app).before_start
    monkeypatch.setattr(type(ctx.app), "before_start",
                        lambda self, c, p: (order.append(("before", marker.read(c.paths) is not None)),
                                            real_before(self, c, p))[1])
    monkeypatch.setattr(type(ctx.app), "after_start",
                        lambda self, c, p: order.append(("after", None)) or None)
    deploy.deploy(ctx, "v2026.9.24")
    assert order == [("before", True), ("after", None)]


def test_after_start_failure_rolls_back(tmp_path, monkeypatch):
    ctx = ops_ctx(tmp_path, monkeypatch)
    monkeypatch.setattr(type(ctx.app), "after_start", lambda self, c, p: "schema mismatch")
    deploy.deploy(ctx, "v2026.9.24")
    assert load(ctx)["current"] == CUR
    assert "failed during deploy: schema mismatch" in ctx.notify.sent[-1].text

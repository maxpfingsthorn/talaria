from talaria import backup, deploy, marker
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
    orig_start = deploy.hermes.start
    monkeypatch.setattr(deploy.hermes, "start",
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

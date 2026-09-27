from talaria import hermes
from talaria.shell import Result
from tests.fakes import make_test_ctx


def active_ctx(tmp_path, restarts=("0",), active="active\n", **kw):
    ctx = make_test_ctx(tmp_path, settle_seconds=10, **kw)
    seq = list(restarts)
    ctx.sh.on("systemctl", "--user", "is-active", out=active)
    ctx.sh.on("systemctl", "--user", "show",
              fn=lambda a, i: Result(0, (seq.pop(0) if len(seq) > 1 else seq[0]) + "\n"))
    return ctx


def test_post_start_check_passes(tmp_path):
    ctx = active_ctx(tmp_path)
    assert hermes.post_start_check(ctx) is None
    assert ctx.clock.slept >= 10


def test_post_start_check_detects_restart(tmp_path):
    ctx = active_ctx(tmp_path, restarts=("0", "0", "1"))
    assert "restarted" in hermes.post_start_check(ctx)


def test_post_start_check_detects_inactive(tmp_path):
    ctx = active_ctx(tmp_path, active="failed\n")
    assert "not active" in hermes.post_start_check(ctx)


def test_post_start_check_requires_auth(tmp_path):
    ctx = active_ctx(tmp_path)
    ctx.http_get = lambda url, timeout=5.0: (200, b'{"auth_required": false}')
    assert "auth" in hermes.post_start_check(ctx)


def test_post_start_check_status_retries_then_fails(tmp_path):
    ctx = active_ctx(tmp_path)
    calls = []
    ctx.http_get = lambda url, timeout=5.0: (calls.append(url), (0, b""))[1]
    reason = hermes.post_start_check(ctx)
    assert "answered nothing" in reason and len(calls) == 12
    assert calls[0] == "http://127.0.0.1:9119/api/status"


def test_status_url_uses_tailscale_ip(tmp_path):
    ctx = active_ctx(tmp_path, dashboard_bind="tailscale", tailscale_ip="100.64.1.2")
    seen = []
    ctx.http_get = lambda url, timeout=5.0: (seen.append(url), (200, b'{"auth_required": true}'))[1]
    hermes.post_start_check(ctx)
    assert seen[0].startswith("http://100.64.1.2:9119/")


def test_start_resets_failed_first(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("systemctl")
    hermes.start(ctx)
    assert ctx.sh.calls == [["systemctl", "--user", "reset-failed", "hermes.service"],
                            ["systemctl", "--user", "start", "hermes.service"]]


def test_config_version(tmp_path):
    (tmp_path / "config.yaml").write_text("model: x\n_config_version: 27\n")
    assert hermes.config_version(tmp_path) == 27
    (tmp_path / "config.yaml").write_text("_config_version: 27\n_config_version: 28\n")
    assert hermes.config_version(tmp_path) is None
    (tmp_path / "config.yaml").unlink()
    assert hermes.config_version(tmp_path) is None

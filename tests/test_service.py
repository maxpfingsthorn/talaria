import pytest

from talaria import service
from talaria.shell import CommandError, Result
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
    assert service.post_start_check(ctx) is None
    assert ctx.clock.slept >= 10


def test_post_start_check_detects_restart(tmp_path):
    ctx = active_ctx(tmp_path, restarts=("0", "0", "1"))
    assert "restarted" in service.post_start_check(ctx)


def test_post_start_check_detects_inactive(tmp_path):
    ctx = active_ctx(tmp_path, active="failed\n")
    assert "not active" in service.post_start_check(ctx)


def test_post_start_check_requires_auth(tmp_path):
    ctx = active_ctx(tmp_path)
    ctx.http_get = lambda url, timeout=5.0: (200, b'{"auth_required": false}')
    assert "auth" in service.post_start_check(ctx)


def test_post_start_check_status_retries_then_fails(tmp_path):
    ctx = active_ctx(tmp_path)
    calls = []
    ctx.http_get = lambda url, timeout=5.0: (calls.append(url), (0, b""))[1]
    reason = service.post_start_check(ctx)
    assert "answered nothing" in reason and len(calls) == 12
    assert calls[0] == "http://127.0.0.1:9119/api/status"


def test_status_url_uses_tailscale_ip(tmp_path):
    ctx = active_ctx(tmp_path, dashboard_bind="tailscale", tailscale_ip="100.64.1.2")
    seen = []
    ctx.http_get = lambda url, timeout=5.0: (seen.append(url), (200, b'{"auth_required": true}'))[1]
    service.post_start_check(ctx)
    assert seen[0].startswith("http://100.64.1.2:9119/")


def test_start_resets_failed_first(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("systemctl")
    service.start(ctx)
    assert ctx.sh.calls == [["systemctl", "--user", "reset-failed", "hermes.service"],
                            ["systemctl", "--user", "start", "hermes.service"]]


def test_config_version(tmp_path):
    (tmp_path / "config.yaml").write_text("model: x\n_config_version: 27\n")
    assert make_test_ctx(tmp_path).app.data_version(tmp_path) == 27
    (tmp_path / "config.yaml").write_text("_config_version: 27\n_config_version: 28\n")
    assert make_test_ctx(tmp_path).app.data_version(tmp_path) is None
    (tmp_path / "config.yaml").unlink()
    assert make_test_ctx(tmp_path).app.data_version(tmp_path) is None


def test_post_start_check_exact_commands_and_timing(tmp_path):
    ctx = active_ctx(tmp_path)
    urls = []
    ctx.http_get = lambda url, timeout: (urls.append((url, timeout)), (200, b'{"auth_required": true}'))[1]
    assert service.post_start_check(ctx) is None
    assert ctx.clock.slept == 10
    assert urls == [("http://127.0.0.1:9119/api/status", 5.0)]
    assert list(zip(ctx.sh.calls, ctx.sh.timeouts)) == [
        (["systemctl", "--user", "show", "-p", "NRestarts", "--value", "hermes.service"], 600),
        (["systemctl", "--user", "is-active", "hermes.service"], 600),
        (["systemctl", "--user", "show", "-p", "NRestarts", "--value", "hermes.service"], 600),
        (["systemctl", "--user", "is-active", "hermes.service"], 600),
        (["systemctl", "--user", "show", "-p", "NRestarts", "--value", "hermes.service"], 600)]


@pytest.mark.parametrize("settle,slept", [(5, 5), (6, 10), (11, 15)])
def test_post_start_check_settle_rounds_up_to_5s(settle, slept, tmp_path):
    ctx = active_ctx(tmp_path)
    ctx.conf.settle_seconds = settle
    service.post_start_check(ctx)
    assert ctx.clock.slept == slept


def test_post_start_check_status_recovers_after_retries(tmp_path):
    ctx = active_ctx(tmp_path)
    answers = [(0, b""), (503, b""), (200, b'{"auth_required": true}')]
    ctx.http_get = lambda url, timeout: answers.pop(0)
    assert service.post_start_check(ctx) is None
    assert ctx.clock.slept == 10 + 10


def test_post_start_check_messages_exact(tmp_path):
    ctx = active_ctx(tmp_path, active="failed\n")
    assert service.post_start_check(ctx) == "hermes.service is not active"
    (tmp_path / "b").mkdir()
    ctx = active_ctx(tmp_path / "b", restarts=("0", "1"))
    assert service.post_start_check(ctx) == "hermes.service restarted"


def test_health_messages_exact(tmp_path):
    ctx = make_test_ctx(tmp_path)
    for answer, msg in [((0, b""), "/api/status answered nothing"),
                        ((502, b""), "/api/status answered 502"),
                        ((200, b"not json"), "/api/status does not report auth_required: true"),
                        ((200, b'{"auth_required": "yes"}'),
                         "/api/status does not report auth_required: true"),
                        ((200, b'{"auth_required": true}'), None)]:
        ctx.http_get = lambda url, timeout, a=answer: a
        assert ctx.app.health(ctx) == msg


def test_nrestarts_empty_is_zero(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("systemctl", "--user", "show", out="\n")
    assert service.nrestarts(ctx) == 0


def test_start_continues_when_reset_failed_fails(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("systemctl").on("systemctl", "--user", "reset-failed", rc=1)
    service.start(ctx)
    assert ctx.sh.calls[-1] == ["systemctl", "--user", "start", "hermes.service"]


def test_stop_checks_the_result(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("systemctl", "--user", "stop", rc=1)
    with pytest.raises(CommandError):
        service.stop(ctx)


def test_stop_and_is_active_exact(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("systemctl").on("systemctl", "--user", "is-active", rc=3, out="inactive\n")
    service.stop(ctx)
    assert service.is_active(ctx) is False
    assert ctx.sh.calls == [["systemctl", "--user", "stop", "hermes.service"],
                            ["systemctl", "--user", "is-active", "hermes.service"]]


def test_config_version_ignores_symlinked_config(tmp_path):
    (tmp_path / "real.yaml").write_text("_config_version: 27\n")
    (tmp_path / "config.yaml").symlink_to(tmp_path / "real.yaml")
    assert make_test_ctx(tmp_path).app.data_version(tmp_path) is None


def test_post_start_check_uses_the_apps_unit_and_health(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.conf.settle_seconds = 5
    ctx.sh.on("systemctl", "--user", "is-active", out="active\n")
    ctx.sh.on("systemctl", "--user", "show", out="0\n")

    class FakeApp:
        unit = "x.service"
        def health(self, c):
            return "not yet"
    ctx.app = FakeApp()
    assert service.post_start_check(ctx) == "not yet"
    assert ctx.sh.called("systemctl", "--user", "is-active", "x.service")

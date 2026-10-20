import copy

import pytest

from talaria import cli, lock, marker, op, restart, state
from tests.fakes import make_test_ctx


def verbs(ctx):
    return [c[2] for c in ctx.sh.calls if c[:2] == ["systemctl", "--user"]]


@pytest.fixture
def rctx(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("systemctl").on("systemctl", "--user", "is-active", out="active\n")
    ctx.down = None
    monkeypatch.setattr(restart.service, "post_start_check", lambda c: ctx.down)
    return ctx


def st0():
    return copy.deepcopy(state.DEFAULT)


def test_restart_stops_starts_checks_and_reports(rctx):
    st = st0()
    restart.run(rctx, st)
    assert verbs(rctx) == ["stop", "reset-failed", "start"]
    assert [m.text for m in rctx.notify.sent] == ["Hermes restarted and running."]
    assert st["op"] is None and state.load(rctx.paths)["op"] is None


def test_restart_records_the_op_before_stopping(rctx, monkeypatch):
    seen = []
    monkeypatch.setattr(restart.service, "stop",
                        lambda c: seen.append(state.load(c.paths)["op"]))
    restart.run(rctx, st0())
    assert seen[0]["op"] == "restart" and seen[0]["changed"] is False
    assert seen[0]["started"] == rctx.now().isoformat()


def test_failed_start_keeps_the_op_for_rollback(rctx):
    rctx.down = "hermes.service is not active"
    st = st0()
    restart.run(rctx, st)
    m = rctx.notify.sent[-1]
    assert m.text == ("Hermes did not come back after the restart. Send /rollback CONFIRM to "
                      "start it again; nothing is restored.")
    assert m.untrusted == [("Last error", "hermes.service is not active")]
    assert state.load(rctx.paths)["op"]["op"] == "restart"


def test_exception_is_reported_and_keeps_the_op(rctx, monkeypatch):
    def boom(c):
        raise RuntimeError("no bus")
    monkeypatch.setattr(restart.service, "start", boom)
    restart.run(rctx, st0())
    assert rctx.notify.sent[-1].untrusted == [("Last error", "RuntimeError: no bus")]
    assert state.load(rctx.paths)["op"]["op"] == "restart"


def test_refused_after_an_interrupted_change(rctx):
    marker.write(rctx.paths, "deploy", "b", {}, rctx.now())
    st = st0()
    restart.run(rctx, st)
    assert verbs(rctx) == []
    assert [m.text for m in rctx.notify.sent] == [
        "Hermes not restarted: an interrupted deploy must be recovered first: send "
        "/rollback CONFIRM."]
    assert st["op"] is None


def test_refused_while_an_op_record_is_open(rctx):
    st = st0()
    st["op"] = {"op": "maintain", "changed": False, "started": rctx.now().isoformat()}
    restart.run(rctx, st)
    assert verbs(rctx) == [] and "interrupted maintain" in rctx.notify.sent[-1].text


def test_cli_restart_runs_under_the_lock(rctx):
    assert cli.main(["restart"], make=lambda: rctx) == 0
    assert verbs(rctx) == ["stop", "reset-failed", "start"]


def test_cli_restart_busy(rctx):
    with lock.op_lock(rctx.paths):
        assert cli.main(["restart"], make=lambda: rctx) == cli.EXIT_BUSY
    assert verbs(rctx) == []


def test_op_restart_routes_to_the_locked_run(rctx, monkeypatch):
    seen = []
    monkeypatch.setattr(op.cli, "run_locked", lambda c, a: (seen.append(vars(a)), 0)[1])
    assert op.main(["restart"], make=lambda: rctx) == 0
    assert seen == [{"cmd": "restart", "timer": False}]


def test_op_restart_takes_no_arguments(rctx):
    assert op.main(["restart", "now"], make=lambda: rctx) == 2


def test_stale_restart_op_is_cleared_once_the_app_runs(rctx):
    from talaria import rollback
    st = st0()
    st["op"] = {"op": "restart", "changed": False, "started": "x"}
    rollback.clear_stale_op(rctx, st)
    assert st["op"] is None and state.load(rctx.paths)["op"] is None


def test_rollback_resumes_an_interrupted_restart(rctx):
    from talaria import rollback
    st = st0()
    st["op"] = {"op": "restart", "changed": False, "started": "x"}
    assert rollback.needs_resume(rctx, st)

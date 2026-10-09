import copy
import io
import subprocess
from datetime import datetime, timezone

import pytest

from talaria import cli, lock, maintain, marker, op, state
from tests.fakes import local_tz, make_test_ctx


@pytest.fixture(autouse=True)
def utc():
    with local_tz():
        yield


def at(ctx, h, m, day=8):
    ctx.clock.t = datetime(2026, 10, day, h, m, tzinfo=timezone.utc)
    ctx.clock.slept = 0.0


def verbs(ctx):
    return [c[2] for c in ctx.sh.calls if c[:2] == ["systemctl", "--user"]]


def st0():
    return copy.deepcopy(state.DEFAULT)


@pytest.fixture
def mctx(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path, maintenance_time="03:30")
    at(ctx, 3, 40)                               # Thursday 03:40, inside 03:30-05:30
    ctx.sh.on("systemctl").on("systemctl", "--user", "is-active", out="active\n")
    monkeypatch.setattr(ctx.app, "has_maintenance", True)
    ctx.hook = {"reason": None, "exc": None, "down": None, "seen": []}

    def hook(c):
        assert c is ctx
        ctx.hook["seen"].append(verbs(c))
        if ctx.hook["exc"]:
            raise ctx.hook["exc"]
        return ctx.hook["reason"]
    monkeypatch.setattr(ctx.app, "maintenance", hook)
    monkeypatch.setattr(maintain.service, "post_start_check", lambda c: ctx.hook["down"])
    return ctx


def test_timer_run_stops_runs_the_hook_starts_and_stays_silent(mctx):
    st = st0()
    maintain.run(mctx, st, timer=True)
    assert mctx.hook["seen"] == [["is-active", "stop"]]
    assert verbs(mctx) == ["is-active", "stop", "reset-failed", "start"]
    assert mctx.notify.sent == []
    assert st["maintenance"] == {"date": "2026-10-08", "ok": True}
    # saved before the hook ran: a crash mid-way does not run it again tonight
    assert state.load(mctx.paths)["maintenance"] == {"date": "2026-10-08", "ok": False}


def test_timer_runs_once_per_window(mctx):
    st = st0()
    maintain.run(mctx, st, timer=True)
    at(mctx, 5, 0)
    maintain.run(mctx, st, timer=True)
    assert len(mctx.hook["seen"]) == 1


@pytest.mark.parametrize("h,m,runs", [(3, 29, False), (3, 30, True), (5, 29, True),
                                      (5, 30, False), (12, 0, False)])
def test_window_is_two_hours_from_maintenance_time(mctx, h, m, runs):
    at(mctx, h, m)
    maintain.run(mctx, st0(), timer=True)
    assert bool(mctx.hook["seen"]) is runs


def test_window_crossing_midnight_runs_once(mctx):
    mctx.conf.maintenance_time = "23:30"
    st = st0()
    at(mctx, 0, 15, day=9)
    maintain.run(mctx, st, timer=True)
    assert st["maintenance"]["date"] == "2026-10-08"
    at(mctx, 1, 0, day=9)
    maintain.run(mctx, st, timer=True)
    assert len(mctx.hook["seen"]) == 1
    at(mctx, 23, 40, day=9)
    maintain.run(mctx, st, timer=True)
    assert st["maintenance"]["date"] == "2026-10-09" and len(mctx.hook["seen"]) == 2


def test_window_start_values(mctx):
    assert maintain.window_start(mctx) == datetime(2026, 10, 8, 3, 30, tzinfo=timezone.utc)
    mctx.conf.maintenance_time = ""
    assert maintain.window_start(mctx) is None


def test_no_window_means_no_timer_runs(mctx):
    mctx.conf.maintenance_time = ""
    maintain.run(mctx, st0(), timer=True)
    assert mctx.sh.calls == [] and mctx.hook["seen"] == []


def test_hook_failure_is_reported_once_and_the_app_started(mctx):
    mctx.hook["reason"] = "dream: phase synthesize failed"
    st = st0()
    maintain.run(mctx, st, timer=True)
    assert verbs(mctx)[-2:] == ["reset-failed", "start"]
    (m,) = mctx.notify.sent
    assert m.text == "Hermes maintenance failed. Talaria tries again next night."
    assert m.untrusted == [("Last error", "dream: phase synthesize failed")]
    assert st["maintenance"] == {"date": "2026-10-08", "ok": False}


def test_hook_exception_still_starts_the_app_and_reports(mctx):
    mctx.hook["exc"] = subprocess.TimeoutExpired(["podman", "run"], 3600)
    st = st0()
    maintain.run(mctx, st, timer=True)
    assert verbs(mctx)[-1] == "start"
    (m,) = mctx.notify.sent
    assert m.untrusted[0][1].startswith("TimeoutExpired: ")
    assert st["maintenance"] == {"date": "2026-10-08", "ok": False}
    maintain.run(mctx, st, timer=True)                   # same night: not again
    assert len(mctx.hook["seen"]) == 1


def test_unhealthy_after_the_start_is_reported(mctx):
    mctx.hook["down"] = "hermes.service is not active"
    maintain.run(mctx, st0(), timer=True)
    assert mctx.notify.sent[0].untrusted == [("Last error", "hermes.service is not active")]


def test_hook_failure_and_unhealthy_name_both(mctx):
    mctx.hook["reason"], mctx.hook["down"] = "boom", "hermes.service restarted"
    maintain.run(mctx, st0(), timer=True)
    assert mctx.notify.sent[0].untrusted == [("Last error", "boom; then hermes.service restarted")]


def test_start_failure_is_reported(mctx):
    mctx.sh.on("systemctl", "--user", "start", rc=1, err="Job failed")
    maintain.run(mctx, st0(), timer=True)
    assert "exited 1" in mctx.notify.sent[0].untrusted[0][1]


def test_timer_skips_an_app_that_is_not_running(mctx):
    mctx.sh.on("systemctl", "--user", "is-active", out="inactive\n")
    st = st0()
    maintain.run(mctx, st, timer=True)
    assert verbs(mctx) == ["is-active"] and st.get("maintenance") is None


@pytest.mark.parametrize("how", ["op", "marker"])
def test_interrupted_change_blocks_maintenance(mctx, how):
    st = st0()
    if how == "op":
        st["op"] = {"op": "deploy", "tag": "v2026.9.24", "started": mctx.now().isoformat()}
    else:
        marker.write(mctx.paths, "deploy", "b", {}, mctx.now())
    maintain.run(mctx, st, timer=True)
    assert verbs(mctx) == ["is-active"] and mctx.notify.sent == []
    maintain.run(mctx, st, timer=False)
    assert mctx.notify.texts() == ["Hermes maintenance not run: an interrupted deploy must be "
                                   "recovered first: send /rollback CONFIRM."]
    assert mctx.hook["seen"] == []


def test_pending_offer_does_not_block_maintenance(mctx):
    st = st0()
    st["pending"] = {"tag": "v2026.9.24"}
    maintain.run(mctx, st, timer=True)
    assert len(mctx.hook["seen"]) == 1


def test_manual_run_ignores_the_window_and_reports_success(mctx):
    mctx.conf.maintenance_time = ""
    at(mctx, 14, 0)
    st = st0()
    maintain.run(mctx, st, timer=False)
    assert mctx.hook["seen"] == [["stop"]]
    assert mctx.notify.texts() == ["Hermes maintenance finished."]
    assert st["maintenance"] == {"date": "2026-10-08", "ok": True}


def test_app_without_maintenance(mctx, monkeypatch):
    monkeypatch.setattr(mctx.app, "has_maintenance", False)
    maintain.run(mctx, st0(), timer=True)
    assert mctx.sh.calls == [] and mctx.notify.sent == []
    maintain.run(mctx, st0(), timer=False)
    assert mctx.notify.texts() == ["Hermes has no maintenance."]


@pytest.fixture
def routed(mctx, monkeypatch):
    seen = []

    def fake(c, st, timer):
        seen.append((c is mctx, timer))
        st["maintenance"] = {"date": "x", "ok": True}
    monkeypatch.setattr(cli.maintain, "run", fake)
    return seen


def test_parser():
    assert vars(cli.build_parser().parse_args(["maintain", "--timer"])) == \
        {"cmd": "maintain", "timer": True}


def test_cli_maintain_runs_under_the_lock_and_saves_state(mctx, routed):
    assert cli.main(["maintain", "--timer"], make=lambda: mctx) == 0
    assert cli.main(["maintain"], make=lambda: mctx) == 0
    assert routed == [(True, True), (True, False)]
    assert state.load(mctx.paths)["maintenance"] == {"date": "x", "ok": True}


def test_busy_timer_maintain_is_silent_manual_is_not(mctx, routed):
    with lock.op_lock(mctx.paths):
        assert cli.main(["maintain", "--timer"], make=lambda: mctx) == 0
        assert mctx.notify.sent == []
        assert cli.main(["maintain"], make=lambda: mctx) == cli.EXIT_BUSY
    assert routed == [] and "Busy" in mctx.notify.sent[-1].text


def test_op_maintain_routes_to_the_locked_command(mctx, routed):
    assert op.main(["maintain", "--timer"], make=lambda: mctx, out=io.StringIO()) == 0
    assert op.main(["maintain"], make=lambda: mctx, out=io.StringIO()) == 0
    assert routed == [(True, True), (True, False)]


@pytest.mark.parametrize("argv", [["maintain", "x"], ["maintain", "--tim"],
                                  ["maintain", "--timer=1"]])
def test_op_refuses_other_maintain_forms(argv):
    out = io.StringIO()
    assert op.main(argv, make=lambda: pytest.fail("no ctx"), out=out) == 2
    assert out.getvalue() == ""

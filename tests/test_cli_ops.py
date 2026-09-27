import pytest

from talaria import cli, lock, state
from tests.fakes import make_test_ctx


@pytest.fixture
def run(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    calls = []
    for mod, name in [("deploy", "deploy"), ("rollback", "rollback_cmd"),
                      ("rollback", "restore_cmd"), ("check", "check"),
                      ("check", "rehearse_tag")]:
        m = __import__(f"talaria.{mod}", fromlist=[name])
        monkeypatch.setattr(m, name, lambda *a, _n=name: calls.append((_n, a[1:])))
    ctx.calls = calls
    return ctx, lambda *argv: cli.main(list(argv), make=lambda: ctx)


def test_deploy_dispatch_validates_tag(run):
    ctx, main = run
    assert main("deploy", "v2026.9.24") == 0
    assert ctx.calls == [("deploy", ("v2026.9.24",))]
    with pytest.raises(SystemExit):
        main("deploy", "v2026.9.24;rm")


def test_rollback_requires_confirm(run, capsys, monkeypatch):
    ctx, main = run
    monkeypatch.setattr(cli.rollback, "describe", lambda c: "would restore X")
    assert main("rollback") == 0
    assert "would restore X" in capsys.readouterr().out and ctx.calls == []
    main("rollback", "--confirm")
    assert ctx.calls == [("rollback_cmd", ())]


def test_restore_validates_id(run):
    ctx, main = run
    with pytest.raises(SystemExit):
        main("restore", "../../etc", "--confirm")
    main("restore", "20260927T043000Z-manual", "--confirm")
    assert ctx.calls == [("restore_cmd", ("20260927T043000Z-manual",))]


def test_check_saves_state(run, monkeypatch):
    ctx, main = run
    def fake_check(c, st):
        st["check_failures"] = 2
    monkeypatch.setattr(cli.check, "check", fake_check)
    main("check")
    assert state.load(ctx.paths)["check_failures"] == 2


def test_main_busy_timer_is_silent(run):
    ctx, main = run
    with lock.op_lock(ctx.paths):
        assert main("check", "--timer") == 0
    assert ctx.notify.sent == [] and ctx.calls == []


def test_main_busy_notifies(run):
    ctx, main = run
    with lock.op_lock(ctx.paths):
        assert main("deploy", "v2026.9.24") == cli.EXIT_BUSY
    assert "Busy" in ctx.notify.sent[-1].text and ctx.calls == []


def test_reject(run):
    ctx, main = run
    st = state.load(ctx.paths)
    st["pending"] = {"tag": "v2026.9.24"}
    state.save(ctx.paths, st)
    assert "Rejected" in cli.reject(ctx, "v2026.9.24")
    st = state.load(ctx.paths)
    assert st["pending"] is None and st["rejected"] == ["v2026.9.24"]


def test_unexpected_error_is_reported_not_raised(run, monkeypatch):
    ctx, main = run
    def boom(c, t):
        raise RuntimeError("kaputt")
    monkeypatch.setattr(cli.deploy, "deploy", boom)
    assert main("deploy", "v2026.9.24") == 1
    assert "kaputt" in ctx.notify.sent[-1].text

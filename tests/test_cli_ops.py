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


# ---- parser and dispatch, exact (mutation testing) ----

import argparse


def parse(*argv):
    return vars(cli.build_parser().parse_args(list(argv)))


def test_parser_setup_flags():
    base = {"cmd": "setup", "plan": False, "user": None, "adopt": None, "dev": False,
            "app": None, "as_service": False, "hub": "talaria", "as_hub": False,
            "register": None, "import_telegram": False, "lock_held": False,
            "no_restart": False}
    assert parse("setup") == base
    assert parse("setup", "--plan", "--user", "h", "--adopt", "u.service", "--dev",
                 "--app", "clawvisor", "--as-service", "--hub", "hub2", "--as-hub",
                 "--register", "clawvisor:h", "--import-telegram", "--lock-held",
                 "--no-restart") == {
        **base, "plan": True, "user": "h", "adopt": "u.service", "dev": True,
        "app": "clawvisor", "as_service": True, "hub": "hub2", "as_hub": True,
        "register": "clawvisor:h", "import_telegram": True, "lock_held": True,
        "no_restart": True}


@pytest.mark.parametrize("argv,expected", [
    (["check"], {"cmd": "check", "timer": False}),
    (["check", "--timer"], {"cmd": "check", "timer": True}),
    (["rehearse", "v2026.1.2"], {"cmd": "rehearse", "tag": "v2026.1.2"}),
    (["deploy", "v2026.1.2"], {"cmd": "deploy", "tag": "v2026.1.2"}),
    (["reject", "v2026.1.2"], {"cmd": "reject", "tag": "v2026.1.2"}),
    (["rollback"], {"cmd": "rollback", "confirm": False}),
    (["rollback", "--confirm"], {"cmd": "rollback", "confirm": True}),
    (["restore", "20260927T043000Z-manual"], {"cmd": "restore", "id": "20260927T043000Z-manual",
                                              "confirm": False}),
    (["self-update", "v0.2.0"], {"cmd": "self-update", "tag": "v0.2.0"}),
    (["set-token"], {"cmd": "set-token"}), (["backup"], {"cmd": "backup"}),
    (["backups"], {"cmd": "backups"}), (["status"], {"cmd": "status"}),
    (["history"], {"cmd": "history"}), (["bot"], {"cmd": "bot"}), (["version"], {"cmd": "version"}),
    (["relay", "hermes", "check", "--timer"], {"cmd": "relay", "app": "hermes",
                                               "op": ["check", "--timer"]}),
    (["update", "v0.6.0"], {"cmd": "update", "tag": "v0.6.0", "offer": False}),
    (["update", "v0.6.0", "--offer"], {"cmd": "update", "tag": "v0.6.0", "offer": True}),
])
def test_parser_commands(argv, expected):
    assert parse(*argv) == expected


@pytest.mark.parametrize("argv", [["rehearse", "x"], ["reject", "latest"], ["self-update", "v1.2"],
                                  ["self-update", "main"], ["restore", "nope"], [],
                                  ["update", "main"], ["update"]])
def test_parser_rejects(argv):
    with pytest.raises(SystemExit):
        parse(*argv)


def test_validator_messages():
    for fn, bad, msg in [(cli._release, "x", "not a release tag: 'x'"),
                         (cli._backup_id, "x", "not a backup id: 'x'"),
                         (cli._semver, "x", "not a Talaria release: 'x'")]:
        with pytest.raises(argparse.ArgumentTypeError) as e:
            fn(bad)
        assert str(e.value) == msg
    assert cli._semver("v1.2.3") == "v1.2.3"


def test_status_and_backups_print(run, capsys, monkeypatch):
    ctx, main = run
    monkeypatch.setattr(cli.status, "status_text", lambda c: "STATUS")
    monkeypatch.setattr(cli.status, "backups_text", lambda c: "BACKUPS")
    assert main("status") == 0 and main("backups") == 0
    assert capsys.readouterr().out == "STATUS\nBACKUPS\n"


def test_reject_prints(run, capsys):
    ctx, main = run
    assert main("reject", "v2026.1.2") == 0
    assert capsys.readouterr().out == "Rejected v2026.1.2. It will not be offered again.\n"


def test_reject_busy(run):
    ctx, main = run
    with lock.op_lock(ctx.paths):
        assert cli.reject(ctx, "v2026.1.2") == "Busy: another operation is running. Try again in a minute."
    assert state.load(ctx.paths)["rejected"] == []


def test_reject_twice_and_other_pending(run):
    ctx, main = run
    st = state.load(ctx.paths)
    st["pending"] = {"tag": "v2026.9.24"}
    state.save(ctx.paths, st)
    cli.reject(ctx, "v2026.1.2")
    cli.reject(ctx, "v2026.1.2")
    st = state.load(ctx.paths)
    assert st["rejected"] == ["v2026.1.2"] and st["pending"] == {"tag": "v2026.9.24"}


def test_restore_describe_prints(run, capsys, monkeypatch):
    ctx, main = run
    monkeypatch.setattr(cli.rollback, "describe_restore", lambda c, i: f"would restore {i}")
    assert main("restore", "20260927T043000Z-manual") == 0
    assert capsys.readouterr().out == "would restore 20260927T043000Z-manual\n"


def test_delegations(run, monkeypatch, capsys):
    ctx, main = run
    from talaria import selfupdate, setup
    seen = []
    monkeypatch.setattr(setup, "setup", lambda a: (seen.append(("setup", a.cmd)), 3)[1])
    monkeypatch.setattr(setup, "set_token", lambda c: (seen.append(("set-token", c is ctx)), 4)[1])
    monkeypatch.setattr(selfupdate, "self_update", lambda c, t: (seen.append(("su", t)), 6)[1])
    assert [main("setup"), main("set-token"), main("bot"), main("self-update", "v0.2.0")] == [3, 4, 1, 6]
    assert seen == [("setup", "setup"), ("set-token", True), ("su", "v0.2.0")]
    assert "the hub runs it" in capsys.readouterr().err


def test_self_update_runs_without_the_lock(run, monkeypatch):
    ctx, main = run
    from talaria import selfupdate
    monkeypatch.setattr(selfupdate, "self_update", lambda c, t: 0)
    with lock.op_lock(ctx.paths):
        assert main("self-update", "v0.2.0") == 0


def test_rehearse_and_history_save_state(run, monkeypatch):
    ctx, main = run
    monkeypatch.setattr(cli.check, "rehearse_tag",
                        lambda c, st, t: st.__setitem__("failed", [t]))
    main("rehearse", "v2026.1.2")
    assert state.load(ctx.paths)["failed"] == ["v2026.1.2"]
    msgs = []
    monkeypatch.setattr(cli.history, "commit", lambda c, st, m: (msgs.append(m),
                                                                 st.__setitem__("history_error", "e")))
    main("history")
    assert msgs == ["manual"] and state.load(ctx.paths)["history_error"] == "e"


def test_manual_backup_exact(run, capsys, monkeypatch):
    ctx, main = run
    ctx.sh.on("systemctl")
    st = state.load(ctx.paths)
    st["current"] = {"tag": "v2026.1.1", "id": "i"}
    state.save(ctx.paths, st)
    assert main("backup") == 0
    assert capsys.readouterr().out == "20260927T043000Z-manual\n"
    assert ctx.sh.calls == [["systemctl", "--user", "stop", "hermes.service"],
                            ["systemctl", "--user", "reset-failed", "hermes.service"],
                            ["systemctl", "--user", "start", "hermes.service"]]
    from talaria import backup
    assert backup.get(ctx, "20260927T043000Z-manual").meta["image"] == {"tag": "v2026.1.1", "id": "i"}


def test_manual_backup_restarts_hermes_on_failure(run, monkeypatch):
    ctx, main = run
    ctx.sh.on("systemctl")
    from talaria import backup
    monkeypatch.setattr(backup, "create", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    assert main("backup") == 1
    assert ctx.sh.calls[-1] == ["systemctl", "--user", "start", "hermes.service"]
    assert ctx.notify.sent[-1].text == "talaria backup failed unexpectedly: disk"


def test_busy_message_exact_and_check_without_timer(run):
    ctx, main = run
    with lock.op_lock(ctx.paths):
        assert main("check") == cli.EXIT_BUSY
    assert ctx.notify.sent[-1].text == "Busy: another operation is running. Try again in a minute."


def test_version_prints(capsys):
    assert cli.main(["version"]) == 0


def test_check_crash_still_saves_state(run, monkeypatch):
    ctx, main = run

    def crash(c, st):
        st["talaria_notified"] = "v9.9.9"
        raise RuntimeError("boom")

    monkeypatch.setattr(cli.check, "check", crash)
    assert main("check", "--timer") == 1
    assert state.load(ctx.paths)["talaria_notified"] == "v9.9.9"


def test_main_defaults_xdg_runtime_dir(run, monkeypatch):
    # `sudo -u hermes talaria status` has no XDG_RUNTIME_DIR; systemctl --user needs it
    import os
    ctx, main = run
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(cli.status, "status_text", lambda c: "S")
    main("status")
    assert os.environ["XDG_RUNTIME_DIR"] == f"/run/user/{os.getuid()}"


def test_main_keeps_existing_xdg_runtime_dir(run, monkeypatch):
    import os
    ctx, main = run
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/custom")
    monkeypatch.setattr(cli.status, "status_text", lambda c: "S")
    main("status")
    assert os.environ["XDG_RUNTIME_DIR"] == "/custom"

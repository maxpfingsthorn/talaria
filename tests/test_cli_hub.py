from types import SimpleNamespace

import pytest

from talaria import cli, hubexec
from tests.fakes import make_test_ctx
from tests.hubfakes import make_hub


@pytest.fixture
def hubmain(tmp_path, monkeypatch):
    hub = make_hub(tmp_path, ("hermes", "clawvisor"))
    seen = []
    from talaria import hubcheck, hubupdate, relay, setup, telegram
    monkeypatch.setattr(telegram, "run", lambda h: (seen.append(("bot", h is hub)), 11)[1])
    monkeypatch.setattr(relay, "main", lambda h, app, argv: (seen.append(("relay", app, argv)), 12)[1])
    monkeypatch.setattr(relay, "status_all", lambda h: "ALL")
    monkeypatch.setattr(hubcheck, "check", lambda h, timer, report=False: (seen.append(("check", timer, report)), 13)[1])
    monkeypatch.setattr(hubcheck, "check_talaria", lambda h: (seen.append(("talaria",)), 18)[1])
    monkeypatch.setattr(hubupdate, "self_update", lambda h, t: (seen.append(("su", t)), 14)[1])
    monkeypatch.setattr(hubupdate, "start_update", lambda h, t: (seen.append(("update", t)), 15)[1])
    monkeypatch.setattr(hubupdate, "offer", lambda h, t: (seen.append(("offer", t)), 16)[1])
    monkeypatch.setattr(setup, "set_token", lambda c: (seen.append(("token", c is hub.ctx)), 17)[1])
    run = lambda *argv: cli.main(list(argv), make=lambda: pytest.fail("app ctx in a hub"),
                                 make_hub=lambda: hub)
    return hub, run, seen


def test_hub_commands(hubmain, capsys):
    hub, run, seen = hubmain
    assert [run("bot"), run("relay", "hermes", "check", "--timer"), run("check", "--timer"),
            run("check"), run("check", "--report"), run("check", "--talaria"),
            run("self-update", "v0.6.0"), run("update", "v0.6.0"),
            run("update", "v0.6.0", "--offer"), run("set-token"), run("status")] == \
        [11, 12, 13, 13, 13, 18, 14, 15, 16, 17, 0]
    assert seen == [("bot", True), ("relay", "hermes", ["check", "--timer"]), ("check", True, False),
                    ("check", False, False), ("check", False, True), ("talaria",), ("su", "v0.6.0"), ("update", "v0.6.0"), ("offer", "v0.6.0"),
                    ("token", True)]
    assert capsys.readouterr().out == "ALL\n"


@pytest.mark.parametrize("argv", [["deploy", "v2026.9.24"], ["backups"], ["rollback"],
                                  ["restore", "20260927T043000Z-manual"], ["backup"], ["history"],
                                  ["rehearse", "v2026.9.24"], ["login-link"], ["reject", "v2026.9.24"]])
def test_app_commands_are_refused_in_a_hub(hubmain, capsys, argv):
    hub, run, seen = hubmain
    assert run(*argv) == 2 and seen == []
    assert capsys.readouterr().err == (f"talaria {argv[0]}: this account is the Talaria hub; app "
                                       "commands run as the app's account or through the bot\n")


def test_transitional_routes_only_the_bot_side(hubmain):
    hub, run, seen = hubmain
    hub.transitional = True
    assert run("bot") == 11 and run("check", "--timer") == 13
    app_dir = hub.ctx.paths.home.parent / "app"
    app_dir.mkdir()
    app_ctx = make_test_ctx(app_dir)
    assert cli.main(["backups"], make=lambda: app_ctx, make_hub=lambda: hub) == 0


@pytest.mark.parametrize("argv", [["relay", "hermes", "status"], ["update", "v0.6.0"]])
def test_hub_only_commands_outside_a_hub(argv, tmp_path, capsys):
    assert cli.main(argv, make=lambda: pytest.fail("no ctx"), make_hub=lambda: None) == 2
    assert capsys.readouterr().err == f"talaria {argv[0]}: this account is not a Talaria hub\n"


def test_injected_app_ctx_never_looks_for_a_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hubexec, "load_hub", lambda *a, **k: pytest.fail("looked for a hub"))
    ctx = make_test_ctx(tmp_path)
    assert cli.main(["backups"], make=lambda: ctx) == 0


def test_default_make_looks_for_a_hub(monkeypatch):
    fake = SimpleNamespace(transitional=False)
    monkeypatch.setattr(hubexec, "load_hub", lambda *a, **k: fake)
    from talaria import telegram
    monkeypatch.setattr(telegram, "run", lambda h: 0 if h is fake else 1)
    assert cli.main(["bot"]) == 0

import argparse
import io

import pytest

from talaria import setup, units
from talaria.conf import parse_kv
from talaria.ctx import Ctx, Paths
from talaria.hubconf import load_hub_conf
from tests.fakes import Clock, FakeNotifier, FakeShell

TOKEN = "123456:" + "a" * 35
PAIR = ("ACTION REQUIRED: in a private chat with your bot, send within 15 minutes:\n"
        "  /pair CODE2345\n")
UNITS = [["systemctl", "--user", "daemon-reload"],
         ["systemctl", "--user", "enable", "--now", "talaria-check.timer",
          "talaria-maintain.timer", "talaria-telegram.service"]]
RESTART = [["systemctl", "--user", "restart", "talaria-telegram.service"]]


def hargs(**kw):
    return argparse.Namespace(**{"register": None, "import_telegram": False, **kw})


def hub_ctx(tmp_path, env=""):
    home = tmp_path / "hub"
    home.mkdir()
    p = Paths(home)
    if env:
        p.conf_dir.mkdir(parents=True)
        p.env_file.write_text(env)
    clock = Clock()
    ctx = Ctx(paths=p, conf=load_hub_conf(p), sh=FakeShell().on("systemctl"),
              notify=FakeNotifier(), sleep=clock.sleep, now=clock.now)
    return ctx


@pytest.fixture
def paired(monkeypatch):
    monkeypatch.setattr(setup.getpass, "getuser", lambda: "talaria")
    monkeypatch.setattr(setup.telegram, "new_code", lambda: "CODE2345")
    seen = []
    monkeypatch.setattr(setup.telegram, "TelegramAPI", lambda base, token: seen.append((base, token)))
    monkeypatch.setattr(setup.telegram, "pair",
                        lambda c, api, code, timeout_s=900, announce=lambda: None: (
                            announce(), {"id": 42, "first_name": "Ann", "username": "ann"})[1])
    return seen


def run(ctx, capsys, **a):
    rc = setup.hub_phase(ctx, hargs(**a))
    return rc, capsys.readouterr().out.replace(str(ctx.paths.home), "~H"), ctx.sh.calls


def test_first_run_registers_and_asks_for_the_token(tmp_path, paired, capsys):
    ctx = hub_ctx(tmp_path)
    rc, out, cmds = run(ctx, capsys, register="hermes:hermes")
    assert rc == 10 and cmds == []
    assert out == ("ACTION REQUIRED: create a Telegram bot: open @BotFather, send /newbot, copy "
                   "the token. Then, in your own terminal (not through an agent), run:\n"
                   "  sudo -u talaria -H ~H/.local/bin/talaria set-token\n")
    assert ctx.paths.hub_conf.read_text() == ("# Talaria hub settings; see README.\n"
                                              "apps = hermes:hermes\n")
    assert (ctx.paths.conf_dir.stat().st_mode & 0o777) == 0o700
    assert (ctx.paths.state_dir.stat().st_mode & 0o777) == 0o700


def test_pairing_then_units_and_bot(tmp_path, paired, capsys):
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\n")
    rc, out, cmds = run(ctx, capsys, register="hermes:hermes")
    assert rc == 0
    assert out == PAIR + "OK: paired with Ann (@ann)\nOK: Talaria hub ready; apps: hermes\n"
    assert cmds == UNITS + RESTART
    assert parse_kv(ctx.paths.env_file.read_text())["TALARIA_TELEGRAM_USER_ID"] == "42"
    assert paired == [(ctx.conf.telegram_api, TOKEN)]
    assert sorted(p.name for p in ctx.paths.units_dir.iterdir()) == list(units.HUB_UNITS)


def test_rerun_unchanged_does_not_restart_the_bot(tmp_path, paired, capsys):
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\nTALARIA_TELEGRAM_USER_ID=42\n")
    run(ctx, capsys, register="hermes:hermes")
    ctx.sh.calls.clear()
    rc, out, cmds = run(ctx, capsys, register="hermes:hermes")
    assert (rc, out, cmds) == (0, "OK: Talaria hub ready; apps: hermes\n", UNITS)


def test_registering_another_app_restarts_the_bot(tmp_path, paired, capsys):
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\nTALARIA_TELEGRAM_USER_ID=42\n")
    run(ctx, capsys, register="hermes:hermes")
    ctx.sh.calls.clear()
    rc, out, cmds = run(ctx, capsys, register="clawvisor:clawvisor")
    assert rc == 0 and out == "OK: Talaria hub ready; apps: hermes, clawvisor\n"
    assert cmds == UNITS + RESTART


def test_without_register_re_renders_only(tmp_path, paired, capsys):
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\nTALARIA_TELEGRAM_USER_ID=42\n")
    rc, out, cmds = run(ctx, capsys)
    assert rc == 0 and out == "OK: Talaria hub ready; apps: none yet\n"
    assert ctx.paths.hub_conf.read_text() == "# Talaria hub settings; see README.\n"


@pytest.mark.parametrize("reg,msg", [
    ("hermes:other", "already registers hermes for the account hermes"),
    ("clawvisor:hermes", "the account hermes is registered twice"),
    ("nope:x", "'nope:x' is not <app>:<user>"),
])
def test_conflicting_registration_stops(tmp_path, paired, capsys, reg, msg):
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\nTALARIA_TELEGRAM_USER_ID=42\n")
    run(ctx, capsys, register="hermes:hermes")
    ctx.sh.calls.clear()
    rc, out, cmds = run(ctx, capsys, register=reg)
    assert rc == 1 and out.startswith("STOP: ") and msg in out and cmds == []


def test_no_pair_message_stops(tmp_path, paired, monkeypatch, capsys):
    monkeypatch.setattr(setup.telegram, "pair",
                        lambda c, api, code, timeout_s=900, announce=lambda: None: (announce(), None)[1])
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\n")
    rc, out, cmds = run(ctx, capsys, register="hermes:hermes")
    assert rc == 10 and cmds == []
    assert out == PAIR + "STOP: no /pair message arrived; run setup again for a new code\n"


def test_paired_user_without_username(tmp_path, paired, monkeypatch, capsys):
    monkeypatch.setattr(setup.telegram, "pair",
                        lambda c, api, code, timeout_s=900, announce=lambda: None: {"id": 7})
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\n")
    rc, out, cmds = run(ctx, capsys, register="hermes:hermes")
    assert "OK: paired with  (@-)\n" in out


def test_setup_dispatches_as_hub(monkeypatch):
    seen = []
    monkeypatch.setattr(setup, "hub_phase", lambda c, a: (seen.append(a.register), 0)[1])
    monkeypatch.setattr("talaria.ctx.make_hub_ctx", lambda *a, **k: "CTX")
    from tests.test_setup import args
    assert setup.setup(args(as_hub=True, register="hermes:hermes")) == 0
    assert seen == ["hermes:hermes"]


def test_import_moves_token_and_owner(tmp_path, capsys, monkeypatch):
    ctx = hub_ctx(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(
        f"TALARIA_TELEGRAM_TOKEN={TOKEN}\nTALARIA_TELEGRAM_USER_ID=42\n"))
    rc, out, cmds = run(ctx, capsys, import_telegram=True)
    assert rc == 0 and cmds == []
    assert out == "OK: bot token and owner moved to the hub (same bot, no new pairing)\n"
    assert parse_kv(ctx.paths.env_file.read_text()) == {"TALARIA_TELEGRAM_TOKEN": TOKEN,
                                                        "TALARIA_TELEGRAM_USER_ID": "42"}
    assert (ctx.paths.env_file.stat().st_mode & 0o777) == 0o600
    assert ctx.paths.hub_conf.read_text() == "# Talaria hub settings; see README.\n"


def test_import_without_an_owner_moves_the_token_only(tmp_path, capsys, monkeypatch):
    ctx = hub_ctx(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(f"TALARIA_TELEGRAM_TOKEN={TOKEN}\n"))
    rc, out, cmds = run(ctx, capsys, import_telegram=True)
    assert rc == 0 and out == "OK: bot token moved to the hub; pair it when setup asks\n"
    assert parse_kv(ctx.paths.env_file.read_text()) == {"TALARIA_TELEGRAM_TOKEN": TOKEN}


def test_import_keeps_the_hubs_own_bot(tmp_path, capsys, monkeypatch):
    ctx = hub_ctx(tmp_path, "TALARIA_TELEGRAM_TOKEN=9:" + "b" * 35 + "\nTALARIA_TELEGRAM_USER_ID=7\n")
    before = ctx.paths.env_file.read_text()
    monkeypatch.setattr("sys.stdin", io.StringIO(f"TALARIA_TELEGRAM_TOKEN={TOKEN}\n"))
    rc, out, cmds = run(ctx, capsys, import_telegram=True)
    assert rc == 0 and out == ("OK: the hub already has a bot; the app's own token is not "
                               "needed any more\n")
    assert ctx.paths.env_file.read_text() == before


@pytest.mark.parametrize("stdin", ["", "nonsense\n", "TALARIA_TELEGRAM_TOKEN=x\n"])
def test_import_refuses_anything_but_a_token(tmp_path, capsys, monkeypatch, stdin):
    ctx = hub_ctx(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    rc, out, cmds = run(ctx, capsys, import_telegram=True)
    assert rc == 1 and out == "STOP: no valid bot token on stdin; nothing changed\n"
    assert not ctx.paths.env_file.exists()

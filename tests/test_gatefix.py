"""Gate-review fixes I1, I2, M1-M6 (one test group per finding)."""
import io
import json

import pytest

from talaria import __version__, hubcheck, hubupdate, lock, op, relay, selfupdate, setup, telegram
from talaria.hubexec import AppEntry
from talaria.shell import Result
from tests.fakes import make_test_ctx
from tests.hubfakes import ex, hello, make_hub, message, reply
from tests.test_setup import args, op_env, svc  # noqa: F401  (fixture)
from tests.test_telegram import calls, cb, spawned, upd, _bot


# ---- I1: a failed self-update restores the previous checkout ----

def test_app_failed_setup_restores_the_previous_checkout(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    d = str(ctx.paths.install_dir)
    ctx.sh.on("git").on("git", "-C", d, "rev-parse", "HEAD", out="abc123\n")
    ctx.sh.on(str(ctx.paths.bin_link), rc=1)
    assert selfupdate.self_update(ctx, "v0.6.0") == 1
    assert ctx.sh.calls[-1] == ["git", "-C", d, "checkout", "-q", "abc123"]
    assert "restored" in capsys.readouterr().err


def test_app_holds_the_op_lock_from_before_fetch_until_after_setup(tmp_path):
    ctx = make_test_ctx(tmp_path)
    d = str(ctx.paths.install_dir)
    busy = []

    def probe(argv, input):
        try:
            with lock.op_lock(ctx.paths):
                busy.append(False)
        except lock.Busy:
            busy.append(True)
        return Result(0, "abc\n")
    ctx.sh.on("git", fn=probe)
    ctx.sh.on(str(ctx.paths.bin_link), fn=probe)
    assert selfupdate.self_update(ctx, "v0.6.0") == 0
    assert busy and all(busy)
    assert [str(ctx.paths.bin_link), "setup", "--as-service", "--lock-held"] in ctx.sh.calls


def test_service_phase_with_lock_held_does_not_take_the_lock(svc, capsys):  # noqa: F811
    with lock.op_lock(svc.paths):
        assert setup.service_phase(svc, args(as_service=True, lock_held=True)) == 0
        assert setup.service_phase(svc, args(as_service=True)) == 1


def hub_with_git(tmp_path, setup_rc=0):
    h = make_hub(tmp_path, ("hermes",))
    d = str(h.ctx.paths.install_dir)
    h.ctx.sh.on("git").on("git", "-C", d, "rev-parse", "HEAD", out="abc123\n")
    h.ctx.sh.on("systemctl").on(str(h.ctx.paths.bin_link), rc=setup_rc, out="")
    ex(h, "hermes").on("self-update", lines=[reply("ok")])
    return h, d


def test_hub_failed_setup_restores_the_previous_checkout(tmp_path):
    h, d = hub_with_git(tmp_path, setup_rc=1)
    assert hubupdate.self_update(h, "v0.6.0") == 1
    assert ["git", "-C", d, "checkout", "-q", "abc123"] in h.ctx.sh.calls
    assert h.ctx.notify.texts()[-1].startswith("Talaria v0.6.0: the hub's setup failed")
    assert "restored" in h.ctx.notify.texts()[-1]
    assert ex(h, "hermes").calls == []


# ---- I2: no downgrades ----

def test_stale_update_button_is_refused(tmp_path):
    ctx, api, b = _bot(tmp_path, ("hermes",))
    b.handle(cb("hub|up:v0.3.0"))
    assert spawned(ctx) == []
    assert "not newer" in calls(api, "answerCallbackQuery")[0]["text"]
    assert calls(api, "editMessageReplyMarkup") == []


def test_update_command_needs_a_newer_tag(tmp_path):
    ctx, api, b = _bot(tmp_path, ("hermes",))
    b.handle(upd(1, f"/update v{__version__}"))
    assert spawned(ctx) == []
    assert f"not newer than the installed v{__version__}" in api.sent()[0]


@pytest.mark.parametrize("tag", ["v0.4.2", "v0.4.99", "v0.1.0"])
def test_op_self_update_refuses_pre_hub_tags(tmp_path, tag):
    ctx = make_test_ctx(tmp_path)
    out = io.StringIO()
    assert op.main(["self-update", tag], make=lambda: ctx, out=out) == 2
    assert "first hub release" in out.getvalue() and ctx.sh.calls == []
    assert op.main(["self-update", tag, "--dry-run"], make=lambda: ctx, out=io.StringIO()) == 2


def test_setup_will_not_move_an_existing_hub_backwards(monkeypatch, tmp_path, capsys):
    sh, run, calls_ = op_env(monkeypatch, tmp_path, installed=True, tag="v0.5.0")
    sh.on("sudo", "-n", "-u", "talaria", "-H", "env", "-u", "XDG_CONFIG_HOME", "-u",
          "XDG_DATA_HOME", "-u", "XDG_STATE_HOME", "-u", "XDG_CACHE_HOME",
          "HOME=/home/talaria", "git", "-C", "/home/talaria/.local/share/talaria",
          "describe", out="v0.5.1\n")
    assert run(args(user="hermes")) == 1
    assert "STOP" in capsys.readouterr().out and calls_ == []


# ---- M1: any relay failure is reported ----

def test_relay_reports_an_unexpected_exception(tmp_path):
    h = make_hub(tmp_path)
    ex(h, "hermes").on("deploy", lines=[message("x", buttons=[["bad"]])])
    assert relay.relay(h, "hermes", ["deploy", "v2026.9.24"]) == 1
    assert "could not follow this operation" in h.ctx.notify.texts()[-1]


def test_relay_reports_a_missing_binary(tmp_path):
    h = make_hub(tmp_path)

    def boom(argv):
        raise FileNotFoundError("sudo")
        yield
    ex(h, "hermes").stream = boom
    assert relay.relay(h, "hermes", ["check"]) == 1
    assert "could not follow" in h.ctx.notify.texts()[-1]


def test_op_survives_a_closed_pipe():
    class Dead:
        def write(self, s): raise BrokenPipeError()
        def flush(self): pass
    op.JsonNotifier(Dead()).send(__import__("talaria.notify").notify.Message("hi"))
    op.emit(Dead(), "reply", text="x")


def test_one_apps_failure_does_not_stop_the_hub_check(tmp_path, monkeypatch):
    h = make_hub(tmp_path, ("hermes", "clawvisor"))
    ex(h, "hermes").on("hello", lines=[hello()]).on("check", lines=[message("a")])
    ex(h, "clawvisor").on("hello", lines=[hello(app="clawvisor")]).on(
        "check", lines=[message("c")])
    real = relay.relay_sent

    def flaky(hub, name, argv):
        if name == "hermes":
            raise RuntimeError("boom")
        return real(hub, name, argv)
    monkeypatch.setattr(hubcheck.relay, "relay_sent", flaky)
    called = []
    monkeypatch.setattr(hubcheck, "talaria_release", lambda hub, force=False: called.append(1) or "current")
    assert hubcheck.check(h, timer=True) == 0
    assert "c" in h.ctx.notify.texts() and called == [1]


# ---- M2: sudoers file names never contain '.' ----

def test_sudoers_file_names_replace_dots():
    text = "\n".join(setup.account_lines("app.x", "adm", False)
                     + setup.op_rule_lines("tal.hub", "app.x"))
    assert "/etc/sudoers.d/talaria-app_x\n" in text + "\n"
    assert "/etc/sudoers.d/talaria-tal_hub-app_x" in text
    assert "sudoers.d/talaria-app.x" not in text


# ---- M3: migration stops while the old bot still runs ----

def test_migration_stops_if_the_old_bot_is_still_active(monkeypatch, tmp_path, capsys):
    sh, run, calls_ = op_env(monkeypatch, tmp_path, installed=True, v04_units=True,
                             v04_token=True)
    s = setup.sudo_as("hermes", "/home/hermes")
    sh.on(*s, "XDG_RUNTIME_DIR=/run/user/1001",
          "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus",
          "systemctl", "--user", "is-active", out="active\n")
    assert run(args(user="hermes")) == 1
    flat = [" ".join(c) for c in sh.calls]
    assert not any(" rm " in c for c in flat) and sh.moves == [] and len(calls_) == 1
    assert "STOP" in capsys.readouterr().out


# ---- M4: one Talaria update at a time; dry runs do not share a worktree ----

def test_second_update_is_refused_while_one_runs(tmp_path):
    h, d = hub_with_git(tmp_path)
    inner, entered = [], []

    def nested(argv, input):
        if not entered:
            entered.append(1)
            inner.append(hubupdate.self_update(h, "v0.6.0"))
        return Result(0, "abc\n")
    h.ctx.sh.on("git", "-C", d, "fetch", fn=nested)
    assert hubupdate.self_update(h, "v0.6.0") == 0
    assert inner == [1]
    assert any("already running" in t for t in h.ctx.notify.texts())


def test_dry_runs_use_their_own_worktree(tmp_path):
    ctx = make_test_ctx(tmp_path)
    reply_ = json.dumps({"v": 1, "kind": "reply", "text": "N", "buttons": []}) + "\n"
    ctx.sh.on("git")
    ctx.sh.on("git", "-C", str(ctx.paths.install_dir), "worktree", "add")
    ctx.sh.on("sh", fn=lambda a, i: Result(0))
    adds = []

    def add(argv, input):
        adds.append(argv[argv.index("--detach") + 1])
        ctx.sh.on(argv[argv.index("--detach") + 1] + "/bin/talaria", out=reply_)
        return Result(0)
    ctx.sh.on("git", "-C", str(ctx.paths.install_dir), "worktree", "add", fn=add)
    selfupdate.dry_run(ctx, "v0.6.0")
    selfupdate.dry_run(ctx, "v0.6.0")
    assert len(set(adds)) == 2


# ---- M5: setup --as-hub during an update does not restart the bot ----

def test_hub_update_runs_setup_without_a_restart(tmp_path):
    h, d = hub_with_git(tmp_path)
    assert hubupdate.self_update(h, "v0.6.0") == 0
    assert [str(h.ctx.paths.bin_link), "setup", "--as-hub", "--no-restart"] in h.ctx.sh.calls


def test_no_restart_flag_skips_the_restart(tmp_path, monkeypatch, capsys):
    from tests.test_setup_hub import hub_ctx, hargs, paired  # noqa: F401
    ctx = hub_ctx(tmp_path, env="TALARIA_TELEGRAM_TOKEN=1:" + "a" * 35
                  + "\nTALARIA_TELEGRAM_USER_ID=42\n")
    monkeypatch.setattr(setup.units, "install_hub_units", lambda c: True)
    assert setup.hub_phase(ctx, hargs(no_restart=True)) == 0
    assert not ctx.sh.called("systemctl", "--user", "restart")
    assert setup.hub_phase(ctx, hargs()) == 0
    assert ctx.sh.called("systemctl", "--user", "restart")


# ---- M6: no hub while another app account still has its own v0.4 bot ----

def test_hub_creation_stops_for_a_v04_bot_in_another_app(monkeypatch, tmp_path, capsys):
    sh, run, calls_ = op_env(monkeypatch, tmp_path, installed=True, hub_exists=False)
    from types import SimpleNamespace
    cv = SimpleNamespace(pw_name="clawvisor", pw_uid=1003, pw_gid=1003, pw_dir="/home/clawvisor")
    sh.on("sudo", "-n", "-u", "clawvisor", "true")
    sh.on("sudo", "-n", "-u", "clawvisor", "test", "-e",
          "/home/clawvisor/.config/systemd/user/talaria-telegram.service")
    real = setup.operator_phase

    def phase(sh_, a, getpwnam, **kw):
        return real(sh_, a, getpwnam=lambda n: cv if n == "clawvisor" else getpwnam(n), **kw)
    monkeypatch.setattr(setup, "operator_phase", phase)
    assert run(args(user="hermes")) == 1
    out = capsys.readouterr().out
    assert "STOP" in out and "clawvisor" in out and "sudo bash" not in out

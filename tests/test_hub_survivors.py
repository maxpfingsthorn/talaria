"""Targeted tests for mutants that survived the v0.5.0 mutation run in the hub modules:
exact text, argv, timeouts and identities that the behavioural tests did not pin down."""
import io
import subprocess
import sys
from pathlib import Path

import pytest

from talaria import __version__, hubcheck, hubconf, hubexec, hubupdate, lock, relay
from talaria.ctx import Paths
from talaria.hubexec import LocalExecutor, NoAnswer, SudoExecutor, Unreachable, load_hub
from talaria.shell import Result
from tests.fakes import FakeShell
from tests.hubfakes import ex, hello, line, make_hub, message, reply


def script(tmp_path, body, name="talaria"):
    p = tmp_path / name
    p.write_text("#!/bin/bash\n" + body + "\n")
    p.chmod(0o755)
    return p


# ---- hubexec ----

def test_ignored_output_is_cut_at_200_characters(capsys):
    hubexec.parse_lines("x" * 250, "hermes")
    assert capsys.readouterr().err == f"[talaria] hermes: ignored output: {'x' * 200}\n"


def test_call_default_timeout_and_unset_returncode_after_a_timeout(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    monkeypatch.setattr(hubexec.subprocess, "run", fake_run)
    e = LocalExecutor("hermes", "/bin/true")
    e.returncode = 7
    with pytest.raises(NoAnswer) as x:
        e.call(["status"])
    assert seen["timeout"] == 60 and e.returncode is None and x.value.args == ("hermes",)


def test_unreachable_names_the_app(tmp_path):
    s = script(tmp_path, "echo 'sudo: a password is required' >&2; exit 1")
    e = SudoExecutor("hermes", "u", "/home/u", sudo=str(s))
    with pytest.raises(Unreachable) as x:
        e.call(["status"])
    assert x.value.args == ("hermes",)
    with pytest.raises(Unreachable) as x:
        list(e.stream(["status"]))
    assert x.value.args == ("hermes",)


def test_sudo_refusal_ignores_leading_whitespace_only():
    e = SudoExecutor("hermes", "u", "/home/u")
    assert e._refused(1, "\n  sudo: a password is required")
    assert not e._refused(1, "ok\nsudo: x")
    assert not e._refused(2, "sudo: x")


def test_stream_reports_ignored_lines_with_the_app_name(tmp_path, capsys):
    s = script(tmp_path, """echo noise; echo '{"v": 1, "kind": "reply", "text": "hi"}'""")
    assert [d["text"] for d in LocalExecutor("clawvisor", s).stream(["status"])] == ["hi"]
    assert capsys.readouterr().err == "[talaria] clawvisor: ignored output: noise\n"


def test_load_hub_without_a_home_uses_the_current_one(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert load_hub() is None


def test_load_hub_executors_know_their_app(tmp_path):
    p = Paths(tmp_path)
    p.conf_dir.mkdir(parents=True)
    p.hub_conf.write_text("apps = hermes:hermes\n")
    pw = type("PW", (), {"pw_dir": "/home/hermes"})
    h = load_hub(tmp_path, getpwnam=lambda u: pw, sh=FakeShell())
    assert h.apps["hermes"].executor.app == "hermes"
    assert h.apps["hermes"].executor.home == "/home/hermes"


def test_transitional_hub_wiring(tmp_path):
    from talaria.notify import TelegramNotifier
    from talaria.shell import Shell
    p = Paths(tmp_path)
    p.conf_dir.mkdir(parents=True)
    p.conf_file.write_text("")
    p.env_file.write_text("TALARIA_TELEGRAM_TOKEN=1:abc\nTALARIA_TELEGRAM_USER_ID=42\n")
    sh = FakeShell()
    h = load_hub(tmp_path, sh=sh)
    assert h.ctx.sh is sh and isinstance(h.ctx.notify, TelegramNotifier)
    assert h.apps["hermes"].executor.app == "hermes"
    assert isinstance(load_hub(tmp_path).ctx.sh, Shell)


# ---- hubconf ----

def test_apps_entry_is_split_at_the_first_colon():
    with pytest.raises(ValueError, match="not a valid account name: 'a:b'"):
        hubconf.parse_apps("hermes:a:b", "f")


def test_bad_apps_line_names_the_file(tmp_path):
    p = Paths(tmp_path)
    p.conf_dir.mkdir(parents=True)
    p.hub_conf.write_text("apps = nonsense\n")
    with pytest.raises(ValueError, match=str(p.hub_conf)):
        hubconf.load_hub_conf(p)
    p.hub_conf.write_text("")
    with pytest.raises(ValueError, match=str(p.hub_conf)):
        hubconf.register_app(p, "hermes", "bad user")


def test_register_creates_the_file_and_its_directory(tmp_path):
    p = Paths(tmp_path)
    assert hubconf.register_app(p, "hermes", "hermes") is True
    assert p.hub_conf.read_text() == "apps = hermes:hermes\n"


# ---- relay ----

def test_block_that_is_not_a_pair_stays_a_string():
    assert relay._block("ab") == "ab"
    assert relay._block(["t", "body"]) == ("t", "body")
    assert relay._block(["t"]) == "['t']"


def test_quick_passes_its_timeout_and_a_missing_text_is_empty(tmp_path):
    h = make_hub(tmp_path)
    ex(h, "hermes").on("status", lines=[{"v": 1, "kind": "reply"}])
    assert relay.quick(h.apps["hermes"], ["status"], timeout=5) == ("", [])
    assert ex(h, "hermes").calls[-1] == ("call", ["status"], 5)


def test_unexpected_relay_error_names_the_operation(tmp_path):
    h = make_hub(tmp_path)
    ex(h, "hermes").on("deploy", exc=RuntimeError("boom"))
    assert relay.relay(h, "hermes", ["deploy", "v1"]) == 1
    assert h.ctx.notify.texts() == ["Hermes: deploy v1 — Talaria could not follow this "
                                    "operation (boom); details in the journal"]


# ---- hubupdate ----

@pytest.fixture
def one(tmp_path):
    h = make_hub(tmp_path)
    h.ctx.sh.on("git", out="abc\n").on(str(h.ctx.paths.bin_link)).on("systemctl")
    return h


def test_app_update_result_flags_and_missing_reply_text(tmp_path):
    h = make_hub(tmp_path)
    e = h.apps["hermes"]
    ex(h, "hermes").on("self-update", exc=Unreachable("hermes"))
    assert hubupdate._update_app(e, "v1") == (False, "Hermes ✗ (sudo rule missing)")
    ex(h, "hermes").on("self-update", lines=[{"v": 1, "kind": "reply"}], rc=3)
    assert hubupdate._update_app(e, "v1") == (False, "Hermes ✗ (exit 3)")
    ex(h, "hermes").on("self-update", lines=[reply("done")], rc=0)
    assert hubupdate._update_app(e, "v1") == (True, "Hermes ✓")


def test_a_second_update_is_refused_while_the_lock_is_held(one):
    one.ctx.paths.state_dir.mkdir(parents=True, exist_ok=True)
    with lock.file_lock(one.ctx.paths.state_dir / "update.lock"):
        assert hubupdate.self_update(one, "v0.6.0") == 1
    assert one.ctx.notify.texts() == ["Talaria v0.6.0: an update is already running."]


def test_restore_and_restart_failures_do_not_raise(one, tmp_path_factory):
    ex(one, "hermes").on("self-update", lines=[reply("x")])
    one.ctx.sh.on("systemctl", rc=1)
    assert hubupdate.self_update(one, "v0.6.0") == 0
    assert one.ctx.notify.texts() == ["Talaria v0.6.0 installed: Hermes ✓"]
    one2 = make_hub(tmp_path_factory.mktemp("two"))
    one2.ctx.sh.on("git", out="abc\n").on(str(one2.ctx.paths.bin_link), rc=1)
    one2.ctx.sh.on("git", "-C", str(one2.ctx.paths.install_dir), "checkout", "-q", "abc", rc=1)
    assert hubupdate.self_update(one2, "v0.6.0") == 1       # the failed restore must not raise


# ---- hubcheck ----

def test_an_app_with_another_protocol_does_not_stop_the_following_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(hubcheck, "latest_semver", lambda sh, repo: "")
    h = make_hub(tmp_path, ("hermes", "clawvisor"))
    ex(h, "hermes").on("hello", lines=[hello(protocol=2)])
    ex(h, "clawvisor").on("hello", lines=[hello(app="clawvisor")]).on(
        "check", lines=[message("cv checked")])
    hubcheck.check(h, timer=False)
    assert h.ctx.notify.texts() == ["Hermes: Talaria versions differ on this host; run /update",
                                    "cv checked"]


def test_unexpected_check_error_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hubcheck, "latest_semver", lambda sh, repo: "")
    h = make_hub(tmp_path)
    ex(h, "hermes").on("hello", exc=RuntimeError("boom"))
    hubcheck.check(h, timer=False)
    assert h.ctx.notify.texts() == ["Hermes: the check failed unexpectedly (boom)"]
    assert "RuntimeError: boom" in capsys.readouterr().err


# ---- op ----

import io  # noqa: E402
import json  # noqa: E402

from talaria import op, rollback, selfupdate, state  # noqa: E402
from talaria.notify import Message  # noqa: E402
from talaria.shell import CommandError  # noqa: E402
from tests.fakes import make_test_ctx  # noqa: E402


class _ClosedPipe:
    def write(self, s):
        raise BrokenPipeError("gone")

    def flush(self):
        pass


def test_emit_survives_a_closed_pipe_and_says_so_on_stderr(capsys):
    op.emit(_ClosedPipe(), "reply", text="x")
    assert capsys.readouterr().err == "[talaria] output closed (gone); carrying on\n"


def test_json_notifier_logs_the_message_text_to_stderr(capsys):
    out = io.StringIO()
    op.JsonNotifier(out).send(Message("hello there", untrusted=[("t", "b")]))
    assert capsys.readouterr().err == "[talaria] message: hello there\n"
    assert json.loads(out.getvalue())["blocks"] == [["t", "b"]]


@pytest.mark.parametrize("argv", [["status", "extra"], ["check", "--bogus"], ["deploy"]])
def test_refused_arguments_name_talaria_op_in_the_usage(argv, capsys):
    assert op.main(argv, make=lambda: pytest.fail("no ctx"), out=io.StringIO()) == 2
    assert capsys.readouterr().err.startswith("usage: talaria op")


@pytest.mark.parametrize("argv", [["-h"], ["status", "-h"], ["check", "--help"]])
def test_help_flags_print_nothing(argv, capsys):
    assert op.main(argv, make=lambda: pytest.fail("no ctx"), out=io.StringIO()) == 2
    assert capsys.readouterr().out == ""


def test_not_allowed_is_logged_and_cut_at_80_characters(capsys):
    op.main(["sh", "-c", "x" * 100], make=None, out=io.StringIO())
    assert capsys.readouterr().err == f"talaria op: not allowed: {('sh -c ' + 'x' * 100)[:80]!r}\n"


def test_wrong_tag_scheme_is_logged(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    assert op.main(["deploy", "v0.9.10"], make=lambda: ctx, out=io.StringIO()) == 2
    assert capsys.readouterr().err == "talaria op: v0.9.10 is not a Hermes release tag\n"


def test_decide_button_hands_the_real_ctx_and_state_to_rollback(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    st = state.load(ctx.paths)
    seen = []
    monkeypatch.setattr(rollback, "needs_resume",
                        lambda c, s: seen.append(("resume", c is ctx, s == st)) or False)
    monkeypatch.setattr(rollback, "target",
                        lambda c, s: seen.append(("target", c is ctx, s == st)) or None)
    op.decide_button(ctx, "rb:20260927T043000Z-manual:1")
    assert seen == [("resume", True, True), ("target", True, True)]
    seen.clear()
    monkeypatch.setattr(rollback, "needs_resume", lambda c, s: True)
    assert op.decide_button(ctx, "rb:resume")[2] == ["rollback", "confirm"]
    assert seen == []                                  # no target lookup while resuming


def test_describe_ops_hand_the_real_ctx_to_rollback(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    rows = [[("Go", "rb:x:1")]]
    monkeypatch.setattr(rollback, "describe", lambda c: "d" if c is ctx else "WRONG")
    monkeypatch.setattr(rollback, "describe_buttons", lambda c: rows if c is ctx else None)
    monkeypatch.setattr(rollback, "describe_restore",
                        lambda c, i: f"r {i}" if c is ctx else "WRONG")
    monkeypatch.setattr(rollback, "describe_restore_buttons",
                        lambda c, i: rows if c is ctx and i == "20260927T043000Z-manual" else None)
    out = io.StringIO()
    assert op.main(["rollback", "describe"], make=lambda: ctx, out=out) == 0
    assert op.main(["restore", "20260927T043000Z-manual", "describe"], make=lambda: ctx,
                   out=out) == 0
    first, second = [json.loads(l) for l in out.getvalue().splitlines()]
    assert (first["text"], first["buttons"]) == ("d", [[["Go", "rb:x:1"]]])
    assert (second["text"], second["buttons"]) == ("r 20260927T043000Z-manual",
                                                   [[["Go", "rb:x:1"]]])


# ---- selfupdate ----

def test_failed_setup_restores_even_if_the_restore_fails(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    d = str(ctx.paths.install_dir)
    ctx.sh.on("git", out="abc\n").on(str(ctx.paths.bin_link), rc=1)
    ctx.sh.on("git", "-C", d, "checkout", "-q", "abc", rc=1)
    assert selfupdate.self_update(ctx, "v0.6.0") == 1
    assert "previous checkout restored" in capsys.readouterr().err


def test_dry_run_details(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path)
    ctx.paths.quadlet.parent.mkdir(parents=True)
    ctx.paths.quadlet.write_text("OLD\n")
    added = []

    def git(argv, input):
        if argv[3:5] == ["worktree", "add"]:
            wt = Path(argv[-2])
            assert not wt.exists()                 # git gets a path that is not there yet
            added.append(wt)
            wt.mkdir(parents=True)
        return Result(1 if argv[3:5] in (["worktree", "prune"], ["worktree", "remove"]) else 0)
    ctx.sh.on("git", fn=git)
    noise = json.dumps({"v": 1, "kind": "message", "text": "not the reply"}) + "\n" + \
        json.dumps({"v": 1, "kind": "reply", "text": "OLD\n", "buttons": []}) + "\n" + "junk\n"
    # the new version's own binary lives in the worktree, whose name is random
    orig = ctx.sh.run

    def run(argv, **kw):
        argv = [str(a) for a in argv]
        if argv[1:] == ["op", "quadlet"]:
            ctx.sh.calls.append(argv)
            ctx.sh.timeouts.append(kw.get("timeout"))
            return Result(0, noise)
        return orig(argv, **kw)
    ctx.sh.run = run
    assert selfupdate.dry_run(ctx, "v0.6.0") is False       # the message line is not the reply
    (wt,) = added
    assert wt.parent == ctx.paths.state_dir and wt.name.startswith("dry-run-")
    assert not wt.exists()                                  # removed even if git left it
    assert capsys.readouterr().err == "[talaria] hermes: ignored output: junk\n"


# ---- telegram ----

from talaria import telegram  # noqa: E402
from talaria.notify import ApiError  # noqa: E402
from tests.test_telegram import ID, _bot, calls, cb, spawned, upd  # noqa: E402


def test_read_form_only_describes_never_confirms():
    rf = telegram.read_form
    assert [rf("/status", []), rf("/backups", []), rf("/check", [])] == ["status", "backups",
                                                                          "check"]
    assert rf("/status", ["x"]) is None and rf("/status", ["v2026.9.24"]) is None
    assert rf("/approve", ["v2026.9.24"]) == "status" == rf("/reject", ["v2026.9.24"])
    assert rf("/approve", []) is None and rf("/rollback", ["v2026.9.24"]) is None
    assert rf("/rollback", []) == "rollback" == rf("/rollback", ["CONFIRM"])
    assert rf("/zzz", []) is None and rf("/zzz", ["CONFIRM"]) == None  # noqa: E711
    assert rf("/restore", [ID]) == f"restore:{ID}" == rf("/restore", [ID, "CONFIRM"])
    assert rf("/restore", []) is None and rf("/restore", ["bad"]) is None
    assert rf("/zzz", [ID]) is None and rf("/restore", [ID, "junk"]) is None


def test_update_command_says_which_tag_is_not_newer(tmp_path):
    ctx, api, b = _bot(tmp_path, ("hermes",))
    b.handle(upd(1, "/update v0.0.1"))
    assert api.sent() == [f"Talaria v0.0.1 is not newer than the installed v{__version__}"]


def test_stale_update_button_says_which_tag_and_version(tmp_path):
    ctx, api, b = _bot(tmp_path, ("hermes",))
    assert b.on_button("hub|up:v0.0.1") == (
        f"Talaria v0.0.1 is not newer than the installed v{__version__}", None)


def test_update_units_are_named_for_the_update(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram.time, "time", lambda: 99.9)
    ctx, api, b = _bot(tmp_path, ("hermes",))
    b.handle(upd(1, "/update v99.0.0"))
    assert ctx.sh.called("systemd-run")[0][4] == "--unit=talaria-update-99"
    assert spawned(ctx)[0][1:] == ["update", "v99.0.0", "--offer"]
    assert b.on_button("hub|up:v99.0.0") == ("Updating", "⬆️ Updating Talaria to v99.0.0…")
    assert ctx.sh.called("systemd-run")[1][4] == "--unit=talaria-update-99"
    assert spawned(ctx)[1][1:] == ["self-update", "v99.0.0"]


def test_button_data_is_split_at_the_first_bar(tmp_path):
    ctx, api, b = _bot(tmp_path, ("hermes",))
    ex(b.hub, "hermes").on("button", lines=[line("button", toast="ok", status=None, run=None)])
    assert b.on_button("hermes|st:a|b") == ("ok", None)
    assert ex(b.hub, "hermes").calls[-1] == ("call", ["button", "st:a|b"], 60)


def test_button_timeout_names_the_app(tmp_path):
    ctx, api, b = _bot(tmp_path, ("hermes",))
    ex(b.hub, "hermes").on("button", exc=NoAnswer("hermes"))
    assert b.on_button("hermes|ap:v1") == ("Hermes did not answer in time", None)


def test_long_operation_unit_is_named_for_app_and_operation(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram.time, "time", lambda: 7)
    ctx, api, b = _bot(tmp_path, ("hermes",))
    ex(b.hub, "hermes").on("button", lines=[line("button", toast="Deploying", status="x",
                                                 run=["deploy", "v2026.9.24"])])
    b.on_button("hermes|ap:v2026.9.24")
    assert ctx.sh.called("systemd-run")[0][4] == "--unit=talaria-hermes-deploy-7"
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "relay", "hermes", "deploy", "v2026.9.24"]]


def test_button_without_a_toast_answers_with_an_empty_text(tmp_path):
    ctx, api, b = _bot(tmp_path, ("hermes",))
    ex(b.hub, "hermes").on("button", lines=[line("button", status="s", run=None)])
    assert b.on_button("hermes|x") == ("", "s")


def test_ignored_button_is_logged_with_its_id(tmp_path, capsys):
    ctx, api, b = _bot(tmp_path, ("hermes",))
    b.handle(cb("hermes|x", user=1))
    assert capsys.readouterr().err == "[talaria] ignored button q1\n"
    assert calls(api, "answerCallbackQuery") == []


def test_button_errors_are_answered_cut_at_200_characters(tmp_path, monkeypatch):
    ctx, api, b = _bot(tmp_path, ("hermes",))

    def boom(data):
        raise RuntimeError("x" * 300)
    monkeypatch.setattr(b, "on_button", boom)
    b.handle(cb("hermes|x"))
    (answer,) = calls(api, "answerCallbackQuery")
    assert answer["text"] == ("Error: " + "x" * 300)[:200] and len(answer["text"]) == 200
    assert calls(api, "editMessageReplyMarkup") == []


def test_toast_is_cut_at_200_characters(tmp_path, monkeypatch):
    ctx, api, b = _bot(tmp_path, ("hermes",))
    monkeypatch.setattr(b, "on_button", lambda data: ("y" * 300, None))
    b.handle(cb("hermes|x"))
    assert calls(api, "answerCallbackQuery")[0]["text"] == "y" * 200


def test_a_command_is_cut_at_the_first_bot_name(tmp_path):
    ctx, api, b = _bot(tmp_path, ("hermes",))
    b.handle(upd(1, "/status@a@b"))
    assert api.sent() == ["Hermes v1, running."]


def test_menu_failure_is_logged_and_startup_goes_on(tmp_path, capsys):
    ctx, api, b = _bot(tmp_path, ("hermes",))
    real = api.call

    def call(method, **p):
        if method == "setMyCommands":
            raise ApiError(400, None, "bad")
        return real(method, **p)
    api.call = call
    b.startup()
    assert capsys.readouterr().err == ("[talaria] could not set the command menu: "
                                       "telegram api status 400: bad\n")


def test_run_starts_polling_from_zero_without_a_backlog(tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
    ex(hub, "hermes").on("hello", lines=[hello()]).on("interrupted")
    offsets = []

    class Stop(Exception):
        pass

    class LoopAPI:
        def __init__(self, base, token):
            pass

        def call(self, method, **p):
            if method == "getUpdates" and p.get("timeout") == 25:
                offsets.append(p["offset"])
                raise Stop()
            return []
    monkeypatch.setattr(telegram, "TelegramAPI", LoopAPI)
    with pytest.raises(Stop):
        telegram.run(hub)
    assert offsets == [0]


def test_run_needs_both_token_and_owner(tmp_path, capsys):
    hub = make_hub(tmp_path)
    hub.ctx.conf.telegram_token, hub.ctx.conf.telegram_user_id = "", 5
    assert telegram.run(hub) == 1
    hub.ctx.conf.telegram_token, hub.ctx.conf.telegram_user_id = "t", 0
    assert telegram.run(hub) == 1


# ---- setup ----

import argparse  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from talaria import setup  # noqa: E402
from tests.test_setup import PW, args, op_env  # noqa: E402,F401
from tests.test_setup_hub import TOKEN, hargs, hub_ctx  # noqa: E402,F401

HSUDO = setup.sudo_as("talaria", "/home/talaria")
SUDO = setup.sudo_as("hermes", "/home/hermes")


def test_older_than_installed_tolerates_a_failing_describe_and_compares_versions(tmp_path):
    sh = FakeShell()
    cmd = HSUDO + ["git", "-C", "/home/talaria/.local/share/talaria", "describe", "--tags",
                   "--exact-match"]
    sh.on(*cmd, rc=128, err="no tag", out="")
    assert setup._older_than_installed(sh, "talaria", "/home/talaria", "v1.0.0") is None
    sh.on(*cmd, out="garbage\n")
    assert setup._older_than_installed(sh, "talaria", "/home/talaria", "v1.0.0") is None
    sh.on(*cmd, out="v0.1.0\n")
    assert setup._older_than_installed(sh, "talaria", "/home/talaria", "v1.0.0") is None
    sh.on(*cmd, out="v2.0.0\n")
    assert setup._older_than_installed(sh, "talaria", "/home/talaria", "v1.0.0") == "v2.0.0"


def test_move_env_silences_the_readers_stderr():
    seen = []

    class P:
        def __init__(self, argv, **kw):
            seen.append((argv, kw))
            self.stdout = SimpleNamespace(close=lambda: None)

        def wait(self):
            return 0
    assert setup.move_env(["r"], ["w"], popen=P) == 0
    assert seen[0][1] == {"stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL}
    assert set(seen[1][1]) == {"stdin"}


@pytest.mark.parametrize("state_,stops", [("active", True), ("activating", True),
                                          ("reloading", True), ("inactive", False),
                                          ("failed", False)])
def test_migrate_waits_until_the_old_bot_has_really_stopped(state_, stops, capsys):
    sh = FakeShell()
    sh.on("sudo", rc=0)
    sh.on("sudo", "-n", "-u", "hermes", "-H", "env", "-u", "XDG_CONFIG_HOME", "-u",
          "XDG_DATA_HOME", "-u", "XDG_STATE_HOME", "-u", "XDG_CACHE_HOME", "HOME=/home/hermes",
          "XDG_RUNTIME_DIR=/run/user/1001",
          "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus", "systemctl", "--user",
          "is-active", out=state_ + "\n", rc=0 if state_ == "active" else 3)
    # disabling a unit that is already gone fails; that must not stop the migration
    sh.on("sudo", "-n", "-u", "hermes", "-H", "env", "-u", "XDG_CONFIG_HOME", "-u",
          "XDG_DATA_HOME", "-u", "XDG_STATE_HOME", "-u", "XDG_CACHE_HOME", "HOME=/home/hermes",
          "XDG_RUNTIME_DIR=/run/user/1001",
          "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus", "systemctl", "--user",
          "disable", rc=1)
    hubpw = SimpleNamespace(pw_dir="/home/talaria", pw_uid=1002)
    pw = SimpleNamespace(pw_dir="/home/hermes", pw_uid=1001)
    rc = setup._migrate(sh, "hermes", pw, "talaria", hubpw, (True, False), None)
    out = capsys.readouterr().out
    if stops:
        assert rc == 1
        assert out == ("STOP: hermes's own bot is still running; stop it (systemctl --user stop "
                       "talaria-telegram.service as that user) and run setup again. "
                       "Nothing was removed.\n")
        assert not any(c[-3:-1] == ["-f", "x"] or "rm" in c for c in sh.calls)
    else:
        assert rc == 0 and out.startswith("OK: stopped hermes's own bot")


def test_the_old_bot_check_is_not_fatal_on_exit_code_3(capsys):
    """`systemctl is-active` exits 3 for an inactive unit; check=True would raise."""
    test_migrate_waits_until_the_old_bot_has_really_stopped("inactive", False, capsys)


def test_plan_for_a_missing_app_account_says_what_will_happen(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path, user_exists=False)
    assert run(args(user="hermes", plan=True)) == 0
    assert capsys.readouterr().out == (
        "PLAN: create the account hermes: setup prints a block to run as root\n"
        "PLAN: then run setup again with --user hermes to install Talaria for it\n")


def test_hub_name_defaults_when_the_namespace_has_none(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path)
    a = args(user="hermes")
    del a.hub
    assert run(a) == 0
    assert calls[1][-4:] == ["setup", "--as-hub", "--register", "hermes:hermes"]
    assert "talaria" in calls[1][3]


def _other_app(monkeypatch, tmp_path, **kw):
    sh, run, calls = op_env(monkeypatch, tmp_path, installed=True, **kw)
    cv = SimpleNamespace(pw_name="clawvisor", pw_uid=1003, pw_gid=1003, pw_dir="/home/clawvisor")
    real = setup.operator_phase

    def phase(sh_, a, getpwnam, **kw_):
        return real(sh_, a, getpwnam=lambda n: cv if n == "clawvisor" else getpwnam(n), **kw_)
    monkeypatch.setattr(setup, "operator_phase", phase)
    return sh, run, cv


def test_a_hub_that_exists_but_is_not_installed_still_waits_for_v04_bots(monkeypatch, tmp_path,
                                                                       capsys):
    sh, run, cv = _other_app(monkeypatch, tmp_path, hub_installed=False)
    sh.on("sudo", "-n", "-u", "clawvisor", "true")
    sh.on("sudo", "-n", "-u", "clawvisor", "test", "-e",
          "/home/clawvisor/.config/systemd/user/talaria-telegram.service")
    assert run(args(user="hermes")) == 1
    assert capsys.readouterr().out == (
        "STOP: clawvisor still runs its own Talaria bot; set clawvisor up first so its bot "
        "moves to the hub: bin/talaria setup --app clawvisor --user clawvisor\n")
    assert ["sudo", "-n", "-u", "clawvisor", "true"] in sh.calls
    assert ["sudo", "-n", "-u", "clawvisor", "test", "-e",
            "/home/clawvisor/.config/systemd/user/talaria-telegram.service"] in sh.calls


def test_another_app_without_a_bot_does_not_block_the_hub(monkeypatch, tmp_path, capsys):
    sh, run, cv = _other_app(monkeypatch, tmp_path, hub_exists=False)
    sh.on("sudo", "-n", "-u", "clawvisor", "true")
    sh.on("sudo", "-n", "-u", "clawvisor", "test", "-e",
          "/home/clawvisor/.config/systemd/user/talaria-telegram.service", rc=1)
    assert run(args(user="hermes")) == 10          # the root paste, not a STOP
    assert "ACTION REQUIRED" in capsys.readouterr().out


def test_ssh_origin_is_refused_with_advice(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path)
    sh.on("git", "-C", str(setup.REPO), "remote", out="git@example.org:o/talaria.git\n")
    assert run(args(user="hermes")) == 1
    assert capsys.readouterr().out == ("STOP: this checkout's origin needs SSH, but the service "
                                       "user has no key; clone Talaria over https\n")


def test_a_hub_on_a_newer_release_stops_setup(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path)
    sh.on(*HSUDO, "git", "-C", "/home/talaria/.local/share/talaria", "describe", out="v9.0.0\n")
    assert run(args(user="hermes")) == 1
    assert capsys.readouterr().out == (
        "STOP: the hub talaria already runs Talaria v9.0.0; this checkout is v0.1.0. "
        "Check out the newer tag and run setup again\n")
    assert calls == []


def test_missing_op_rule_is_detected_after_leading_whitespace(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path, probe_rc=1,
                            probe_err="\n  sudo: a password is required\n")
    assert run(args(user="hermes")) == 10
    assert "paste this into your terminal" in capsys.readouterr().out


def test_unanswering_app_shows_only_the_last_300_characters(monkeypatch, tmp_path, capsys):
    sh, run, calls = op_env(monkeypatch, tmp_path, probe_rc=2,
                            probe_err="a" * 100 + "b" * 300 + "\n")
    assert run(args(user="hermes")) == 1
    assert capsys.readouterr().out.endswith("\nSTOP: Talaria for hermes does not answer: "
                                            + "b" * 300 + "\n")


def test_handoff_names_the_app_only_when_it_was_given(monkeypatch, tmp_path):
    sh, run, calls = op_env(monkeypatch, tmp_path)
    real = setup.operator_phase
    monkeypatch.setattr(setup, "operator_phase",
                        lambda *a, **kw: real(*a, explicit_app=False, **kw))
    assert run(args(user="hermes")) == 0
    assert calls[0][-2:] == ["setup", "--as-service"]


def test_done_is_flushed(monkeypatch, tmp_path):
    sh, run, calls = op_env(monkeypatch, tmp_path)
    flushed = []

    class Out(io.StringIO):
        def flush(self):
            flushed.append(self.getvalue())
    monkeypatch.setattr(sys, "stdout", Out())
    assert run(args(user="hermes")) == 0
    assert flushed and flushed[-1].endswith("DONE\n")


def test_pairing_gets_the_real_ctx_api_and_code(tmp_path, monkeypatch, capsys):
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\n")
    ctx.paths.hub_conf.write_text("")
    api = object()
    seen = []
    monkeypatch.setattr(setup.telegram, "new_code", lambda: "CODE2345")

    def pair(c, a, code, timeout_s=900, announce=lambda: None):
        seen.append((c is ctx, a is api, code, timeout_s))
        return {"id": 42, "first_name": "Ann", "username": "ann"}
    monkeypatch.setattr(setup.telegram, "pair", pair)
    assert setup.hub_phase(ctx, hargs(), api=api) == 0
    assert seen == [(True, True, "CODE2345", 900)]
    assert ctx.conf.telegram_user_id == 42


def test_hub_phase_without_the_optional_flags(tmp_path, monkeypatch, capsys):
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\nTALARIA_TELEGRAM_USER_ID=42\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("not a token"))
    a = argparse.Namespace(register=None)               # no import_telegram, no no_restart
    assert setup.hub_phase(ctx, a) == 0
    assert capsys.readouterr().out == "OK: Talaria hub ready; apps: none yet\n"
    ctx.sh.calls.clear()
    assert setup.hub_phase(ctx, a) == 0                 # nothing changed, nothing registered
    assert not ctx.sh.called("systemctl", "--user", "restart")


def test_register_splits_at_the_first_colon(tmp_path, capsys):
    ctx = hub_ctx(tmp_path, f"TALARIA_TELEGRAM_TOKEN={TOKEN}\n")
    assert setup.hub_phase(ctx, hargs(register="hermes:a:b")) == 1
    assert "not a valid account name: 'a:b'" in capsys.readouterr().out

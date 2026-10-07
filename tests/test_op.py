import argparse
import io
import json
import os

import pytest

from talaria import __version__, backup, cli, lock, op, rollback, state
from talaria.notify import Message
from tests.fakes import make_test_ctx


def lines(buf):
    return [json.loads(l) for l in buf.getvalue().splitlines()]


@pytest.fixture
def opx(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("systemctl", "--user", "is-active", out="active\n")

    def run(*argv):
        out = io.StringIO()
        rc = op.main(list(argv), make=lambda: ctx, out=out)
        return rc, lines(out)
    return ctx, run


def pending(ctx, tag="v2026.9.24"):
    st = state.load(ctx.paths)
    st["pending"] = {"tag": tag}
    state.save(ctx.paths, st)


def test_hello(opx):
    ctx, run = opx
    assert run("hello") == (0, [{"v": 1, "kind": "hello", "protocol": 1, "app": "hermes",
                                 "title": "Hermes", "version": __version__}])


@pytest.mark.parametrize("argv", [
    [], ["setup"], ["set-token"], ["login-link"], ["bot"], ["relay", "hermes", "status"],
    ["adopt"], ["backup"], ["history"], ["rehearse", "v2026.9.24"], ["sh"], ["Status"],
    ["status", "extra"], ["status", "--timer"], ["status", "-h"], ["-h"], ["check", "--tim"],
    ["check", "--timer=1"], ["deploy"], ["deploy", "v2026.9.24;id"], ["deploy", "$(id)"],
    ["reject", "latest"], ["rollback"], ["rollback", "CONFIRM"], ["rollback", "confirm", "x"],
    ["restore", "../x", "describe"], ["restore", "20260927T043000Z-manual"],
    ["restore", "20260927T043000Z-manual", "CONFIRM"], ["button"], ["button", "a", "b"],
    ["hello", "x"], ["interrupted", "x"],
])
def test_anything_else_exits_2_without_side_effects(argv, capsys):
    out = io.StringIO()
    rc = op.main(argv, make=lambda: pytest.fail("no ctx for a refused op"), out=out)
    assert rc == 2 and out.getvalue() == ""


def test_other_apps_tag_scheme_is_refused(opx):
    ctx, run = opx
    assert run("deploy", "v0.9.10") == (2, [])
    assert run("reject", "v0.9.10") == (2, [])
    assert ctx.sh.calls == [] and state.load(ctx.paths)["rejected"] == []


def test_status_and_backups_reply(opx):
    ctx, run = opx
    rc, out = run("status")
    assert rc == 0 and out[0]["kind"] == "reply" and out[0]["text"].startswith("Hermes unknown")
    assert out[0]["buttons"] == []
    assert run("backups") == (0, [{"v": 1, "kind": "reply", "text": "No backups yet.",
                                   "buttons": []}])


def test_interrupted_is_silent_unless_interrupted(opx):
    ctx, run = opx
    assert run("interrupted") == (0, [])
    st = state.load(ctx.paths)
    st["op"] = {"op": "deploy", "tag": "v2026.9.24", "started": ctx.now().isoformat()}
    state.save(ctx.paths, st)
    rc, out = run("interrupted")
    assert rc == 0 and out[0]["kind"] == "reply" and "Interrupted deploy v2026.9.24" in out[0]["text"]


def test_reject_replies_and_records(opx):
    ctx, run = opx
    assert run("reject", "v2026.9.24") == (0, [{
        "v": 1, "kind": "reply", "text": "Rejected v2026.9.24. It will not be offered again.",
        "buttons": []}])
    assert state.load(ctx.paths)["rejected"] == ["v2026.9.24"]


def test_rollback_describe_carries_its_button(opx, monkeypatch):
    ctx, run = opx
    monkeypatch.setattr(op.rollback, "describe", lambda c: "would restore")
    monkeypatch.setattr(op.rollback, "describe_buttons", lambda c: [[("Roll back", "rb:B:1")]])
    assert run("rollback", "describe") == (0, [{"v": 1, "kind": "reply", "text": "would restore",
                                                "buttons": [[["Roll back", "rb:B:1"]]]}])


def test_restore_describe(opx):
    ctx, run = opx
    assert run("restore", "20260927T043000Z-manual", "describe") == (0, [{
        "v": 1, "kind": "reply", "text": "No backup 20260927T043000Z-manual. /backups lists them.",
        "buttons": []}])


@pytest.mark.parametrize("argv,ns", [
    (["check"], dict(cmd="check", timer=False)),
    (["check", "--timer"], dict(cmd="check", timer=True)),
    (["deploy", "v2026.9.24"], dict(cmd="deploy", tag="v2026.9.24", timer=False)),
    (["rollback", "confirm"], dict(cmd="rollback", confirm=True, timer=False)),
    (["restore", "20260927T043000Z-manual", "confirm"],
     dict(cmd="restore", id="20260927T043000Z-manual", confirm=True, timer=False)),
])
def test_long_ops_run_under_the_lock_like_the_cli(opx, monkeypatch, argv, ns):
    ctx, run = opx
    seen = []
    monkeypatch.setattr(op.cli, "run_locked", lambda c, a: (seen.append((c is ctx, vars(a))), 3)[1])
    assert run(*argv) == (3, [])
    assert seen == [(True, ns)]


def test_messages_become_json_lines_and_stray_prints_go_to_stderr(opx, monkeypatch, capsys):
    ctx, run = opx

    def deploy(c, tag):
        print("noise from a library")
        c.notify.send(Message("Deployed Hermes v2026.9.24.", untrusted=[("Log", "body"), "raw"],
                              commands=["/rollback"], buttons=[[("Roll back", "rb:x:1")]]))
    monkeypatch.setattr(cli.deploy, "deploy", deploy)
    assert run("deploy", "v2026.9.24") == (0, [{
        "v": 1, "kind": "message", "text": "Deployed Hermes v2026.9.24.",
        "blocks": [["Log", "body"], "raw"], "commands": ["/rollback"],
        "buttons": [[["Roll back", "rb:x:1"]]]}])
    err = capsys.readouterr().err
    assert "noise from a library" in err and "[talaria] message: Deployed Hermes" in err


def test_busy_deploy_sends_busy_and_exits_75(opx):
    ctx, run = opx
    with lock.op_lock(ctx.paths):
        rc, out = run("deploy", "v2026.9.24")
    assert rc == 75 and [o["text"] for o in out] == [
        "Busy: another operation is running. Try again in a minute."]


def test_busy_timer_check_is_silent(opx):
    ctx, run = opx
    with lock.op_lock(ctx.paths):
        assert run("check", "--timer") == (0, [])


def test_button_op_emits_the_decision(opx):
    ctx, run = opx
    pending(ctx)
    assert run("button", "ap:v2026.9.24") == (0, [{
        "v": 1, "kind": "button", "toast": "Deploying",
        "status": "✅ Approved — deploying v2026.9.24", "run": ["deploy", "v2026.9.24"]}])


def test_cli_routes_op_with_this_users_runtime_dir(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/99999")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/99999/bus")
    seen = []
    monkeypatch.setattr(op, "main", lambda argv: (seen.append(
        (argv, os.environ["XDG_RUNTIME_DIR"], "DBUS_SESSION_BUS_ADDRESS" in os.environ)), 7)[1])
    assert cli.main(["op", "hello"]) == 7
    assert seen == [(["hello"], f"/run/user/{os.getuid()}", False)]


# ---- the button decision (moved here from the v0.4 bot) ----

def test_approve_button(tmp_path):
    ctx = make_test_ctx(tmp_path)
    pending(ctx)
    assert op.decide_button(ctx, "ap:v2026.9.24") == (
        "Deploying", "✅ Approved — deploying v2026.9.24", ["deploy", "v2026.9.24"])


def test_stale_approve_button(tmp_path):
    ctx = make_test_ctx(tmp_path)
    pending(ctx, "v2026.10.1")
    assert op.decide_button(ctx, "ap:v2026.9.24") == (op.STALE, "⌛ Out of date", None)
    assert op.STALE == "Out of date — send /status"


def test_reject_button(tmp_path):
    ctx = make_test_ctx(tmp_path)
    pending(ctx)
    assert op.decide_button(ctx, "rj:v2026.9.24") == ("Rejected", "❌ Rejected v2026.9.24", None)
    assert state.load(ctx.paths)["rejected"] == ["v2026.9.24"]


def test_reject_while_busy_keeps_the_buttons(tmp_path):
    ctx = make_test_ctx(tmp_path)
    pending(ctx)
    with lock.op_lock(ctx.paths):
        assert op.decide_button(ctx, "rj:v2026.9.24") == ("Busy, try again in a minute", None, None)


def test_rollback_button_must_match_current_target(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    monkeypatch.setattr(op.rollback, "needs_resume", lambda c, st: False)
    monkeypatch.setattr(op.rollback, "target", lambda c, st: ("20260927T043000Z-pre-v2", {"tag": "v1"}))
    now = rollback.stamp(ctx)
    assert op.decide_button(ctx, f"rb:20260927T043000Z-other:{now}") == (op.STALE, "⌛ Out of date", None)
    assert op.decide_button(ctx, f"rb:20260927T043000Z-pre-v2:{now}") == (
        "Rolling back", "↩️ Rolling back…", ["rollback", "confirm"])


def test_resume_button(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    monkeypatch.setattr(op.rollback, "needs_resume", lambda c, st: True)
    assert op.decide_button(ctx, "rb:resume")[2] == ["rollback", "confirm"]
    monkeypatch.setattr(op.rollback, "needs_resume", lambda c, st: False)
    monkeypatch.setattr(op.rollback, "target", lambda c, st: None)
    assert op.decide_button(ctx, "rb:resume") == (op.STALE, "⌛ Out of date", None)


def test_restore_button(tmp_path):
    ctx = make_test_ctx(tmp_path)
    assert op.decide_button(ctx, "rs:20260927T043000Z-manual")[2] is None    # no such backup
    bk = backup.create(ctx, "manual", None)
    assert op.decide_button(ctx, f"rs:{bk.id}:{rollback.stamp(ctx)}") == (
        "Restoring", f"↩️ Restoring {bk.id}…", ["restore", bk.id, "confirm"])


@pytest.mark.parametrize("age, fresh", [(0, True), (60, True), (61, False), (-1, False)])
def test_rollback_and_restore_buttons_expire(tmp_path, monkeypatch, age, fresh):
    ctx = make_test_ctx(tmp_path)
    bk = backup.create(ctx, "manual", None)
    monkeypatch.setattr(op.rollback, "needs_resume", lambda c, st: False)
    monkeypatch.setattr(op.rollback, "target", lambda c, st: (bk.id, {"tag": "v1"}))
    sent = rollback.stamp(ctx) - age
    runs = [op.decide_button(ctx, f"{k}:{bk.id}:{sent}")[2] for k in ("rb", "rs")]
    assert (runs == [["rollback", "confirm"], ["restore", bk.id, "confirm"]]) is fresh
    if not fresh:
        assert runs == [None, None]


@pytest.mark.parametrize("data", ["rb:{id}", "rs:{id}", "rb:{id}:x", "rs:{id}:1:2"])
def test_buttons_without_a_valid_minute_are_stale(tmp_path, monkeypatch, data):
    ctx = make_test_ctx(tmp_path)
    bk = backup.create(ctx, "manual", None)
    monkeypatch.setattr(op.rollback, "needs_resume", lambda c, st: False)
    monkeypatch.setattr(op.rollback, "target", lambda c, st: (bk.id, {"tag": "v1"}))
    assert op.decide_button(ctx, data.format(id=bk.id)) == (op.STALE, "⌛ Out of date", None)


@pytest.mark.parametrize("data", ["xx:1", "ap:latest;rm", "rs:../x", "rb:", "", None])
def test_malformed_buttons(tmp_path, data):
    assert op.decide_button(make_test_ctx(tmp_path), data) == ("Unknown button", None, None)


def test_done_button(tmp_path):
    assert op.decide_button(make_test_ctx(tmp_path), "done") == ("Already handled", None, None)

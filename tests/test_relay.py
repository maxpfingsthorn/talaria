# tests/test_relay.py
import pytest

from talaria import relay
from talaria.hubexec import NoAnswer, Unreachable
from talaria.notify import keyboard
from tests.hubfakes import ex, hello, make_hub, message, reply


def test_to_message_prefixes_buttons_and_names_the_app_in_commands():
    m = relay.to_message("clawvisor", message(
        "Clawvisor v0.9.10 is ready to deploy", blocks=[["Migrations", "055.sql"], "raw"],
        commands=["/approve v0.9.10", "/reject v0.9.10", "/rollback"],
        buttons=[[("Approve v0.9.10", "ap:v0.9.10"), ("Reject", "rj:v0.9.10")]]))
    assert m.text == "Clawvisor v0.9.10 is ready to deploy"
    assert m.untrusted == [("Migrations", "055.sql"), "raw"]
    assert m.commands == ["/approve clawvisor v0.9.10", "/reject clawvisor v0.9.10",
                          "/rollback clawvisor"]
    assert m.buttons == [[("Approve v0.9.10", "clawvisor|ap:v0.9.10"),
                          ("Reject", "clawvisor|rj:v0.9.10")]]


def test_to_message_tolerates_missing_fields():
    m = relay.to_message("hermes", {"v": 1, "kind": "message"})
    assert (m.text, m.untrusted, m.commands, m.buttons) == ("", [], [], [])


def test_with_app_leaves_non_commands_alone():
    assert relay.with_app("hermes", ["see README", "/status"]) == ["see README", "/status hermes"]


@pytest.mark.parametrize("app,data", [
    ("hermes", "rb:20261231T235959Z-pre-v2026.12.31.99-2:29999999"),
    ("hermes", "rs:20261231T235959Z-pre-v2026.12.31.99-2:29999999"),
    ("clawvisor", "rb:20261231T235959Z-pre-rollback-12:29999999"),
    ("clawvisor", "ap:v10.100.100"),
])
def test_longest_real_callback_data_still_fits(app, data):
    rows = relay.prefix(app, [[("x", data)]])
    assert len(rows[0][0][1].encode()) <= 64
    assert keyboard(rows) == {"inline_keyboard": [[{"text": "x", "callback_data": f"{app}|{data}"}]]}


@pytest.fixture
def h(tmp_path):
    return make_hub(tmp_path, ("hermes", "clawvisor"))


def test_relay_sends_each_message_as_it_arrives(h):
    ex(h, "hermes").on("deploy", lines=[message("Deploying"), reply("ignored"),
                                        message("Deployed Hermes v2026.9.24.", commands=["/rollback"])])
    assert relay.relay(h, "hermes", ["deploy", "v2026.9.24"]) == 0
    assert h.ctx.notify.texts() == ["Deploying", "Deployed Hermes v2026.9.24."]
    assert h.ctx.notify.sent[1].commands == ["/rollback hermes"]
    assert ex(h, "hermes").calls == [("stream", ["deploy", "v2026.9.24"], None)]


def test_crash_without_a_message_is_reported(h):
    ex(h, "hermes").on("deploy", rc=1)
    assert relay.relay(h, "hermes", ["deploy", "v2026.9.24"]) == 1
    assert h.ctx.notify.texts() == ["Hermes: deploy v2026.9.24 failed unexpectedly (exit 1)"]


def test_failure_after_a_message_adds_nothing(h):
    ex(h, "clawvisor").on("rollback", lines=[message("Busy: another operation is running.")], rc=75)
    assert relay.relay(h, "clawvisor", ["rollback", "confirm"]) == 75
    assert h.ctx.notify.texts() == ["Busy: another operation is running."]


def test_unreachable_app(h):
    ex(h, "clawvisor").on("check", exc=Unreachable("clawvisor"))
    assert relay.relay(h, "clawvisor", ["check"]) == 1
    assert h.ctx.notify.texts() == [
        "Clawvisor: Talaria cannot reach this app (sudo rule missing); run setup again"]


def test_quick(h):
    e = h.apps["hermes"]
    ex(h, "hermes").on("rollback", lines=[reply("would restore", [[("Roll back", "rb:B:1")]])])
    assert relay.quick(e, ["rollback", "describe"]) == ("would restore",
                                                        [[("Roll back", "hermes|rb:B:1")]])
    assert ex(h, "hermes").calls[-1] == ("call", ["rollback", "describe"], 60)
    ex(h, "hermes").on("interrupted")
    assert relay.quick(e, ["interrupted"]) == ("", [])
    ex(h, "hermes").on("status", rc=2)
    assert relay.quick(e, ["status"]) == ("Hermes: status failed unexpectedly (exit 2)", [])
    ex(h, "hermes").on("status", exc=Unreachable("hermes"))
    assert relay.quick(e, ["status"])[0] == relay.unreachable_text("Hermes")
    ex(h, "hermes").on("status", exc=NoAnswer("hermes"))
    assert relay.quick(e, ["status"]) == ("Hermes did not answer in time", [])


def test_hello(h):
    e = h.apps["clawvisor"]
    ex(h, "clawvisor").on("hello", lines=[hello(app="clawvisor")])
    assert relay.hello(e) is None
    ex(h, "clawvisor").on("hello", lines=[hello(protocol=2, app="clawvisor")])
    assert relay.hello(e) == relay.VERSIONS
    ex(h, "clawvisor").on("hello", rc=2)
    assert relay.hello(e) == "Talaria versions differ on this host; run /update"
    ex(h, "clawvisor").on("hello", exc=Unreachable("clawvisor"))
    assert relay.hello(e) == relay.unreachable_text("Clawvisor")
    ex(h, "clawvisor").on("hello", exc=NoAnswer("clawvisor"))
    assert relay.hello(e) == "Clawvisor did not answer in time"


def test_status_all(h, tmp_path):
    ex(h, "hermes").on("status", lines=[reply("Hermes v1, running.")])
    ex(h, "clawvisor").on("status", lines=[reply("Clawvisor v0.9.10, running.")])
    assert relay.status_all(h) == "Hermes v1, running.\n\nClawvisor v0.9.10, running."
    assert relay.status_all(h, {"clawvisor"}) == (
        "Hermes v1, running.\n\nClawvisor: Talaria versions differ on this host; run /update")
    h.apps.clear()
    assert relay.status_all(h) == "No apps registered."


def test_main_refuses_an_unknown_app_or_no_op(h, capsys):
    assert relay.main(h, "nope", ["status"]) == 2
    assert relay.main(h, "hermes", []) == 2
    assert capsys.readouterr().err == ("talaria relay: unknown app 'nope'; apps: hermes, clawvisor\n"
                                       "talaria relay: no operation given\n")
    ex(h, "hermes").on("check")
    assert relay.main(h, "hermes", ["check", "--timer"]) == 0
    assert ex(h, "hermes").ops() == [["check", "--timer"]]

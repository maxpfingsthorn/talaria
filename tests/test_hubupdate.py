import pytest

from talaria import __version__, hubupdate
from talaria.hubexec import NoAnswer, Unreachable
from talaria.shell import Result
from tests.hubfakes import ex, make_hub, reply


@pytest.fixture
def h2(tmp_path):
    events = []
    h = make_hub(tmp_path, ("hermes", "clawvisor"), log=events)
    sh = h.ctx.sh
    sh.on("git", fn=lambda argv, input: (events.append(("git", argv[3])), Result(0))[1])
    sh.on(str(h.ctx.paths.bin_link), fn=lambda argv, input: (events.append(("setup",)),
                                                             Result(0, "OK: hub units\n"))[1])
    sh.on("systemctl", fn=lambda argv, input: (events.append(("restart",)), Result(0))[1])
    ex(h, "hermes").on("self-update", lines=[reply("Talaria v0.6.0 installed.")])
    ex(h, "clawvisor").on("self-update", lines=[reply("Talaria v0.6.0 installed.")])
    h.events = events
    return h


def test_hub_first_then_each_app_then_the_bot(h2, capsys):
    assert hubupdate.self_update(h2, "v0.6.0") == 0
    assert h2.events == [("git", "fetch"), ("git", "checkout"), ("setup",),
                         ("hermes", "stream", ("self-update", "v0.6.0")),
                         ("clawvisor", "stream", ("self-update", "v0.6.0")), ("restart",)]
    d = str(h2.ctx.paths.install_dir)
    assert list(zip(h2.ctx.sh.calls, h2.ctx.sh.timeouts)) == [
        (["git", "-C", d, "fetch", "-q", "--tags", "origin"], 600),
        (["git", "-C", d, "checkout", "-q", "v0.6.0"], None),
        ([str(h2.ctx.paths.bin_link), "setup", "--as-hub"], 600),
        (["systemctl", "--user", "restart", "talaria-telegram.service"], None)]
    assert h2.ctx.notify.texts() == ["Talaria v0.6.0 installed: Hermes ✓ · Clawvisor ✓"]
    assert capsys.readouterr().out == "OK: hub units\n"


def test_partial_failure_keeps_going_and_names_the_reason(h2):
    ex(h2, "clawvisor").on("self-update", lines=[
        reply("self-update failed (exit 1); details in the journal")], rc=1)
    ex(h2, "hermes").on("self-update", exc=Unreachable("hermes"))
    assert hubupdate.self_update(h2, "v0.6.0") == 1
    assert h2.ctx.notify.texts() == [
        "Talaria v0.6.0 installed: Hermes ✗ (sudo rule missing) · "
        "Clawvisor ✗ (self-update failed (exit 1); details in the journal)"]
    assert h2.events[-1] == ("restart",)


def test_app_failure_without_a_reply_names_the_exit_code(h2):
    ex(h2, "clawvisor").on("self-update", rc=75)
    hubupdate.self_update(h2, "v0.6.0")
    assert h2.ctx.notify.texts() == ["Talaria v0.6.0 installed: Hermes ✓ · Clawvisor ✗ (exit 75)"]


def test_hub_checkout_failure_changes_nothing(h2, capsys):
    h2.ctx.sh.on("git", "-C", str(h2.ctx.paths.install_dir), "checkout", rc=1, err="no such ref")
    assert hubupdate.self_update(h2, "v0.6.0") == 1
    (text,) = h2.ctx.notify.texts()
    assert text.startswith("Talaria v0.6.0 was not installed: the hub could not check it out (")
    assert text.endswith("). Nothing changed.") and "no such ref" in text
    assert not any(e[0] in ("hermes", "clawvisor", "restart") for e in h2.events)
    assert text in capsys.readouterr().err


def test_hub_setup_failure_stops_before_the_apps(h2):
    h2.ctx.sh.on(str(h2.ctx.paths.bin_link), rc=1, out="STOP: x\n")
    assert hubupdate.self_update(h2, "v0.6.0") == 1
    assert h2.ctx.notify.texts() == ["Talaria v0.6.0: the hub's setup failed (exit 1); apps were "
                                     "not updated. Details in the journal."]
    assert not any(e[0] in ("hermes", "clawvisor", "restart") for e in h2.events)


def test_transitional_update_skips_the_hub_install(tmp_path):
    events = []
    h = make_hub(tmp_path, ("hermes",), transitional=True, log=events)
    h.ctx.sh.on("systemctl", fn=lambda argv, input: (events.append(("restart",)), Result(0))[1])
    ex(h, "hermes").on("self-update", lines=[reply("Talaria v0.6.0 installed.")])
    assert hubupdate.self_update(h, "v0.6.0") == 0
    assert events == [("hermes", "stream", ("self-update", "v0.6.0")), ("restart",)]
    assert h.ctx.notify.texts() == ["Talaria v0.6.0 installed: Hermes ✓"]


def test_would_restart(tmp_path):
    h = make_hub(tmp_path)
    e = h.apps["hermes"]
    ex(h, "hermes").on("self-update", lines=[dict(reply("would restart: yes"), restart=True)])
    assert hubupdate.would_restart(e, "v0.6.0") == "yes"
    ex(h, "hermes").on("self-update", lines=[reply("dry run failed: x")], rc=1)
    assert hubupdate.would_restart(e, "v0.6.0") == "unknown"
    ex(h, "hermes").on("self-update", exc=Unreachable("hermes"))
    assert hubupdate.would_restart(e, "v0.6.0") == "unknown (sudo rule missing)"
    ex(h, "hermes").on("self-update", exc=NoAnswer("hermes"))
    assert hubupdate.would_restart(e, "v0.6.0") == "unknown (no answer)"


def test_offer_text_and_button(tmp_path):
    h = make_hub(tmp_path)
    ex(h, "hermes").on("self-update", lines=[dict(reply("would restart: no"), restart=False)])
    assert hubupdate.offer(h, "v0.6.0") == 0
    (m,) = h.ctx.notify.sent
    assert m.text == f"Talaria v0.6.0 is available (installed v{__version__}). Hermes would restart: no"
    assert m.buttons == [[("Update Talaria to v0.6.0", "hub|up:v0.6.0")]]


def test_start_update_runs_in_a_transient_unit(tmp_path, monkeypatch, capsys):
    h = make_hub(tmp_path)
    h.ctx.sh.on("systemd-run")
    monkeypatch.setattr(hubupdate.time, "time", lambda: 1234.5)
    assert hubupdate.start_update(h, "v0.6.0") == 0
    assert h.ctx.sh.calls == [["systemd-run", "--user", "--collect", "--quiet",
                               "--unit=talaria-update-1234", str(h.ctx.paths.bin_link),
                               "self-update", "v0.6.0"]]
    assert capsys.readouterr().out == ("Updating Talaria to v0.6.0 in the background; the bot "
                                       "reports the result.\n")

import json

import pytest

from talaria import __version__, hubcheck
from talaria.hubexec import Unreachable
from talaria.shell import CommandError, Result
from tests.hubfakes import ex, hello, make_hub, message, reply


@pytest.fixture
def h2(tmp_path, monkeypatch):
    log = []
    h = make_hub(tmp_path, ("hermes", "clawvisor"), log=log)
    for n in h.apps:
        ex(h, n).on("hello", lines=[hello(app=n)]).on("check", lines=[message(f"{n} checked")])
    h.latest = f"v{__version__}"
    monkeypatch.setattr(hubcheck, "latest_semver",
                        lambda sh, repo: (sh is h.ctx.sh and repo == h.ctx.conf.talaria_repo)
                        and h.latest)
    h.log = log
    return h


def test_timer_checks_each_app_in_order(h2):
    assert hubcheck.check(h2, timer=True) == 0
    assert h2.log == [("hermes", "call", ("hello",)), ("hermes", "stream", ("check", "--timer")),
                      ("clawvisor", "call", ("hello",)),
                      ("clawvisor", "stream", ("check", "--timer"))]
    assert h2.ctx.notify.texts() == ["hermes checked", "clawvisor checked"]


def test_manual_check_has_no_timer_flag(h2):
    hubcheck.check(h2, timer=False)
    assert ("hermes", "stream", ("check",)) in h2.log


def test_app_with_another_protocol_is_reported_and_skipped(h2):
    ex(h2, "clawvisor").on("hello", lines=[hello(protocol=2, app="clawvisor")])
    hubcheck.check(h2, timer=True)
    assert ("clawvisor", "stream", ("check", "--timer")) not in h2.log
    assert h2.ctx.notify.texts() == [
        "hermes checked", "Clawvisor: Talaria versions differ on this host; run /update"]


def test_unreachable_app_is_reported(h2):
    ex(h2, "hermes").on("hello", exc=Unreachable("hermes"))
    hubcheck.check(h2, timer=True)
    assert h2.ctx.notify.texts()[0] == ("Hermes: Talaria cannot reach this app (sudo rule "
                                        "missing); run setup again")


def test_new_talaria_release_is_offered_once_with_dry_runs(h2):
    h2.latest = "v99.0.0"
    ex(h2, "hermes").on("self-update", lines=[dict(reply("would restart: no"), restart=False)])
    ex(h2, "clawvisor").on("self-update", lines=[dict(reply("would restart: yes"), restart=True)])
    hubcheck.check(h2, timer=True)
    hubcheck.check(h2, timer=True)
    offers = [m for m in h2.ctx.notify.sent if m.text.startswith("Talaria v99.0.0")]
    assert len(offers) == 1
    assert offers[0].text == (f"Talaria v99.0.0 is available (installed v{__version__}). "
                              "Hermes would restart: no · Clawvisor would restart: yes")
    assert offers[0].buttons == [[("Update Talaria to v99.0.0", "hub|up:v99.0.0")]]
    assert ("call", ["self-update", "v99.0.0", "--dry-run"], 900) in ex(h2, "hermes").calls
    assert json.loads(h2.ctx.paths.hub_state.read_text()) == {"talaria_notified": "v99.0.0"}


@pytest.mark.parametrize("latest", [None, f"v{__version__}", "v0.0.1"])
def test_no_offer_without_a_newer_release(h2, latest):
    h2.latest = latest
    hubcheck.check(h2, timer=True)
    assert not any(t.startswith("Talaria") for t in h2.ctx.notify.texts())
    assert not h2.ctx.paths.hub_state.exists()


def test_offline_release_lookup_is_silent(h2, monkeypatch):
    monkeypatch.setattr(hubcheck, "latest_semver",
                        lambda sh, repo: (_ for _ in ()).throw(CommandError(["git"], Result(1))))
    hubcheck.check(h2, timer=True)
    assert h2.ctx.notify.texts() == ["hermes checked", "clawvisor checked"]


def test_corrupt_hub_state_is_treated_as_empty(h2):
    h2.ctx.paths.hub_state.parent.mkdir(parents=True, exist_ok=True)
    h2.ctx.paths.hub_state.write_text("{nope")
    assert hubcheck.load_state(h2.ctx.paths) == {}


def test_transitional_mode_reminds_daily_on_the_timer_only(tmp_path, monkeypatch):
    h = make_hub(tmp_path, ("clawvisor",), transitional=True)
    ex(h, "clawvisor").on("hello", lines=[hello(app="clawvisor")]).on("check")
    monkeypatch.setattr(hubcheck, "latest_semver", lambda sh, repo: None)
    monkeypatch.setattr(hubcheck.getpass, "getuser", lambda: "clawvisor")
    hubcheck.check(h, timer=True)
    hubcheck.check(h, timer=False)
    assert h.ctx.notify.texts() == [
        "Talaria v0.5 needs a one-time move of the bot to its own user: run "
        "`bin/talaria setup --app clawvisor --user clawvisor` as the operator."]

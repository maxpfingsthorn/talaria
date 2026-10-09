import pytest

from talaria import apps, cli, hubmaintain, units
from talaria.hubexec import Unreachable
from tests.hubfakes import ex, make_hub, message
from tests.test_units import _fixed_ctx, _hub_ctx


@pytest.fixture
def h2(tmp_path, monkeypatch):
    log = []
    h = make_hub(tmp_path, ("hermes", "clawvisor"), log=log)
    monkeypatch.setattr(apps.get("clawvisor"), "has_maintenance", True)
    h.log = log
    return h


def test_timer_asks_only_apps_with_maintenance(h2):
    ex(h2, "clawvisor").on("maintain", lines=[])
    assert hubmaintain.maintain(h2, timer=True) == 0
    assert h2.log == [("clawvisor", "stream", ("maintain", "--timer"))]
    assert h2.ctx.notify.texts() == []


def test_manual_run_has_no_timer_flag_and_forwards_messages(h2):
    ex(h2, "clawvisor").on("maintain", lines=[message("Clawvisor maintenance finished.")])
    hubmaintain.maintain(h2, timer=False)
    assert h2.log == [("clawvisor", "stream", ("maintain",))]
    assert h2.ctx.notify.texts() == ["Clawvisor maintenance finished."]


def test_failure_message_from_the_app_keeps_its_block(h2):
    ex(h2, "clawvisor").on("maintain", lines=[message(
        "Clawvisor maintenance failed. Talaria tries again next night.",
        blocks=[["Last error", "boom"]])])
    hubmaintain.maintain(h2, timer=True)
    (m,) = h2.ctx.notify.sent
    assert m.untrusted == [("Last error", "boom")]


def test_unreachable_or_old_app_goes_to_the_journal_only(h2, capsys):
    ex(h2, "clawvisor").on("maintain", exc=Unreachable("clawvisor"))
    assert hubmaintain.maintain(h2, timer=True) == 0
    assert h2.ctx.notify.sent == []
    assert "[talaria] clawvisor: maintain failed: Unreachable('clawvisor')" in capsys.readouterr().err
    ex(h2, "clawvisor").on("maintain", rc=2, err="talaria op: not allowed: 'maintain --timer'")
    hubmaintain.maintain(h2, timer=True)
    assert h2.ctx.notify.sent == []
    assert capsys.readouterr().err == ("[talaria] clawvisor: maintain exited 2: talaria op: "
                                       "not allowed: 'maintain --timer'\n")


def test_one_failing_app_does_not_stop_the_next(tmp_path, monkeypatch):
    log = []
    h = make_hub(tmp_path, ("hermes", "clawvisor"), log=log)
    monkeypatch.setattr(apps.get("hermes"), "has_maintenance", True)
    monkeypatch.setattr(apps.get("clawvisor"), "has_maintenance", True)
    ex(h, "hermes").on("maintain", exc=RuntimeError("boom"))
    ex(h, "clawvisor").on("maintain", lines=[])
    hubmaintain.maintain(h, timer=True)
    assert [a for a, _, _ in log] == ["hermes", "clawvisor"]


def test_cli_routes_maintain_in_a_hub(tmp_path, monkeypatch):
    hub = make_hub(tmp_path, ("hermes",))
    seen = []
    monkeypatch.setattr(hubmaintain, "maintain",
                        lambda h, timer: (seen.append((h is hub, timer)), 0)[1])

    def run(*argv):
        return cli.main(list(argv), make=lambda: pytest.fail("app ctx in a hub"),
                        make_hub=lambda: hub)
    assert run("maintain", "--timer") == 0 and run("maintain") == 0
    assert seen == [(True, True), (True, False)]


def test_hub_units_include_the_maintenance_timer():
    r = units.render_hub_units(_hub_ctx())
    assert sorted(r) == list(units.HUB_UNITS)
    assert r["talaria-maintain.service"] == (
        "[Unit]\nDescription=Talaria: nightly app maintenance\n\n[Service]\nType=oneshot\n"
        "ExecStart=%h/.local/bin/talaria maintain --timer\n")
    assert r["talaria-maintain.timer"] == (
        "[Unit]\nDescription=Talaria: maintenance window check\n\n[Timer]\n"
        "OnCalendar=*:0/15\nPersistent=false\n\n[Install]\nWantedBy=timers.target\n")


def test_v04_app_units_stay_three():
    assert sorted(units.render_units(_fixed_ctx())) == list(units.TALARIA_UNITS)

import io

import pytest

from talaria import apps, check, cli, op
from talaria.conf import DAYS, load_conf
from talaria.ctx import Paths, local_now
from tests.fakes import local_tz, make_test_ctx


@pytest.fixture(autouse=True)
def utc():
    with local_tz():
        yield


def conf_with(tmp_path, text, app="hermes"):
    p = Paths(tmp_path, app)
    p.conf_dir.mkdir(parents=True, exist_ok=True)
    p.conf_file.write_text(text)
    return load_conf(p)


def test_days_constant():
    assert DAYS == ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def test_defaults_are_every_day_and_no_window(tmp_path):
    c = load_conf(Paths(tmp_path))
    assert (c.check_days, c.maintenance_time) == ((), "")


def test_days_and_time_parse(tmp_path):
    c = conf_with(tmp_path, "check.days = Mon thu\nmaintenance.time = 03:30\n")
    assert (c.check_days, c.maintenance_time) == (("mon", "thu"), "03:30")


@pytest.mark.parametrize("line,msg", [
    ("check.days = mon funday", "check.days: 'funday' is not one of mon tue wed thu fri sat sun"),
    ("maintenance.time = 3:30", "maintenance.time must be HH:MM"),
    ("maintenance.time = 24:00", "maintenance.time must be HH:MM"),
    ("maintenance.time = 03:60", "maintenance.time must be HH:MM"),
    ("maintenance.time = 03:30 ", None),
])
def test_bad_values_are_errors_even_at_runtime(tmp_path, line, msg):
    if msg is None:     # parse_kv trims; a trailing space is fine
        assert conf_with(tmp_path, line + "\n").maintenance_time == "03:30"
        return
    with pytest.raises(ValueError, match=msg):
        conf_with(tmp_path, line + "\n")


def test_adapter_defaults_apply_unless_set(tmp_path, monkeypatch):
    cv = apps.get("clawvisor")
    monkeypatch.setattr(cv, "default_check_days", ("mon", "thu"))
    monkeypatch.setattr(cv, "default_maintenance_time", "01:30")
    c = conf_with(tmp_path, "app = clawvisor\n", app="clawvisor")
    assert (c.check_days, c.maintenance_time) == (("mon", "thu"), "01:30")
    c = conf_with(tmp_path, "app = clawvisor\ncheck.days =\nmaintenance.time =\n", app="clawvisor")
    assert (c.check_days, c.maintenance_time) == ((), "")


@pytest.mark.parametrize("days,due", [((), True), (("sun",), True), (("mon", "thu"), False)])
def test_due_today_uses_the_local_weekday(tmp_path, days, due):
    ctx = make_test_ctx(tmp_path, check_days=days)     # the test clock: Sunday 2026-09-27
    assert check.due_today(ctx) is due


def test_local_now_is_aware_local_time(tmp_path):
    ctx = make_test_ctx(tmp_path)
    assert local_now(ctx).hour == 4 and local_now(ctx).tzinfo is not None
    with local_tz("Europe/Berlin"):
        assert local_now(ctx).hour == 6       # 04:30 UTC is 06:30 CEST


@pytest.fixture
def gate(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path, check_days=("mon",))     # Sunday: not a check day
    seen = []
    monkeypatch.setattr(cli.check, "check", lambda c, st: seen.append("check"))
    monkeypatch.setattr(cli.history, "commit", lambda c, st, msg: seen.append(("history", msg)))
    return ctx, seen


def test_timer_check_outside_check_days_only_commits_history(gate):
    ctx, seen = gate
    assert cli.main(["check", "--timer"], make=lambda: ctx) == 0
    assert seen == [("history", "daily")]


def test_on_demand_check_ignores_check_days(gate):
    ctx, seen = gate
    assert cli.main(["check"], make=lambda: ctx) == 0
    assert seen == ["check"]


def test_timer_check_on_a_check_day_runs(gate):
    ctx, seen = gate
    ctx.conf.check_days = ("sun",)
    assert cli.main(["check", "--timer"], make=lambda: ctx) == 0
    assert seen == ["check"]


def test_op_check_timer_is_gated_the_same_way(gate):
    ctx, seen = gate
    assert op.main(["check", "--timer"], make=lambda: ctx, out=io.StringIO()) == 0
    assert seen == [("history", "daily")]

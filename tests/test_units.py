from pathlib import Path

import pytest

from talaria import apps, units
from talaria.conf import load_conf
from talaria.ctx import Ctx, Paths
from tests.fakes import make_test_ctx

GOLDEN = Path(__file__).parent / "golden"
# A fixed, nonexistent home: the rendered text embeds this path, so it must be stable
# across runs to match the golden fixtures byte for byte (unlike pytest's tmp_path).
FIXED_HOME = Path("/home/talaria-fixture")


def _fixed_ctx(app: str = "hermes", **overrides):
    paths = Paths(FIXED_HOME, app)
    conf = load_conf(paths)
    for k, v in overrides.items():
        setattr(conf, k, v)
    return Ctx(paths=paths, conf=conf, sh=None, notify=None, app=apps.get(app))


def test_quadlet_loopback(tmp_path):
    ctx = make_test_ctx(tmp_path)
    q = units.render_quadlet(ctx)
    assert f"Volume={ctx.conf.data_dir}:/opt/data" in q
    assert "PublishPort=127.0.0.1:9119:9119" in q
    assert f"ExecCondition=/bin/sh -c 'test ! -e \"{ctx.paths.marker}\"'" in q
    assert "ExecStartPre" not in q
    assert "HERMES_SKIP_CONFIG_MIGRATION=1" in q and "INSECURE" not in q
    assert "${" not in q


def test_quadlet_tailscale_waits_for_address(tmp_path):
    ctx = make_test_ctx(tmp_path, dashboard_bind="tailscale", tailscale_ip="100.64.1.2")
    q = units.render_quadlet(ctx)
    assert "PublishPort=100.64.1.2:9119:9119" in q
    assert "ExecStartPre=/usr/bin/timeout 120 /bin/sh -c" in q and '" 100.64.1.2/"' in q


def test_timer_time(tmp_path):
    ctx = make_test_ctx(tmp_path, check_time="03:15")
    assert "OnCalendar=*-*-* 03:15" in units.render_units(ctx)["talaria-check.timer"]


def test_install_reports_quadlet_change(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("systemctl")
    assert units.install_units(ctx) is True
    assert units.install_units(ctx) is False
    assert ctx.paths.quadlet.exists()
    assert (ctx.paths.units_dir / "talaria-telegram.service").exists()
    assert ctx.sh.called("systemctl", "--user", "daemon-reload")


def test_quadlet_runs_the_gateway(tmp_path):
    # the official image runs the interactive CLI when given no command (main-wrapper.sh)
    ctx = make_test_ctx(tmp_path)
    assert "\nExec=gateway run\n" in units.render_quadlet(ctx)


def test_quadlet_refuses_empty_bind_address(tmp_path):
    ctx = make_test_ctx(tmp_path, dashboard_bind="tailscale", tailscale_ip="")
    with pytest.raises(ValueError, match="no address"):
        units.render_quadlet(ctx)


def test_add_hosts_rendered_as_addhost_lines(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.conf.add_hosts = ("clawvisor:100.64.0.1",)
    q = units.render_quadlet(ctx)
    assert "\nAddHost=clawvisor:100.64.0.1\n" in q


@pytest.mark.parametrize("bad", [
    "clawvisor", "a b:1.2.3.4", "x:not-an-ip", "x:1.2.3.4\nExec=sh",
    "x:999.1.1.1",                       # octet out of range
    "x:\uff11\uff10\uff10.\uff16\uff14.\uff10.\uff11",   # full-width digits (１００.６４.０.１)
    "x: 100.64.0.1", "x:100.64.0.1 ",    # leading/trailing whitespace
])
def test_bad_add_hosts_refused(tmp_path, bad):
    ctx = make_test_ctx(tmp_path)
    ctx.conf.add_hosts = (bad,)
    with pytest.raises(ValueError, match="add_hosts"):
        units.render_quadlet(ctx)


def test_add_hosts_accepts_clawvisor_tailscale_ip(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.conf.add_hosts = ("clawvisor:100.83.113.68",)
    q = units.render_quadlet(ctx)
    assert "\nAddHost=clawvisor:100.83.113.68\n" in q


def test_hermes_quadlet_unchanged_without_add_hosts(tmp_path):
    ctx = make_test_ctx(tmp_path)
    assert "AddHost" not in units.render_quadlet(ctx)


def test_two_add_hosts_rendered_exact_text(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.conf.add_hosts = ("clawvisor:100.83.113.68", "db:10.0.0.5")
    q = units.render_quadlet(ctx)
    assert ("\nAddHost=clawvisor:100.83.113.68\nAddHost=db:10.0.0.5\nExec=gateway run\n") in q


# ---- byte-for-byte golden tests (final review I1 / adversarial #2) ----
#
# These pin the one property self-update depends on to not restart Hermes on every
# existing install: the rendered Quadlet and unit files for a v0.2.5-shaped conf must
# be *exactly* what main (ec151ec, pre-adapters) rendered. The fixtures were produced
# by rendering main's code with a fixed, nonexistent home path in a throwaway worktree
# (never committed), then diffed byte-for-byte against this tree's render of the same
# configs before being saved under tests/golden/.


def test_default_quadlet_and_units_match_main_byte_for_byte():
    ctx = _fixed_ctx()
    assert units.render_quadlet(ctx) == (GOLDEN / "default.hermes.container").read_text()
    rendered = units.render_units(ctx)
    for name in units.TALARIA_UNITS:
        assert rendered[name] == (GOLDEN / f"default.{name}").read_text()


def test_tailscale_quadlet_and_units_match_main_byte_for_byte():
    ctx = _fixed_ctx(dashboard_bind="tailscale", tailscale_ip="100.83.113.68",
                      dashboard_port=9120, check_time="03:15")
    assert units.render_quadlet(ctx) == (GOLDEN / "tailscale.hermes.container").read_text()
    rendered = units.render_units(ctx)
    for name in units.TALARIA_UNITS:
        assert rendered[name] == (GOLDEN / f"tailscale.{name}").read_text()


def test_quadlet_literal_ip_publishes_and_waits(tmp_path):
    ctx = make_test_ctx(tmp_path, dashboard_bind="10.254.254.1")
    q = units.render_quadlet(ctx)
    assert "PublishPort=10.254.254.1:9119:9119" in q
    assert "ExecStartPre=/usr/bin/timeout 120 /bin/sh -c" in q and '" 10.254.254.1/"' in q


@pytest.mark.parametrize("app", ["hermes", "clawvisor"])
def test_quadlet_host_loopback(app):
    on = units.render_quadlet(_fixed_ctx(app, host_loopback=True))
    off = units.render_quadlet(_fixed_ctx(app))
    line = "Network=slirp4netns:allow_host_loopback=true\n"
    assert line in on and line not in off and "${" not in on
    assert on.replace(line, "") == off
    assert on.index("[Container]") < on.index(line) < on.index("[Service]")

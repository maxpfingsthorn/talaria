import pytest

from talaria import units
from tests.fakes import make_test_ctx


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


@pytest.mark.parametrize("bad", ["clawvisor", "a b:1.2.3.4", "x:not-an-ip", "x:1.2.3.4\nExec=sh"])
def test_bad_add_hosts_refused(tmp_path, bad):
    ctx = make_test_ctx(tmp_path)
    ctx.conf.add_hosts = (bad,)
    with pytest.raises(ValueError, match="add_hosts"):
        units.render_quadlet(ctx)


def test_hermes_quadlet_unchanged_without_add_hosts(tmp_path):
    ctx = make_test_ctx(tmp_path)
    assert "AddHost" not in units.render_quadlet(ctx)

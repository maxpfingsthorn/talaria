from __future__ import annotations

import ipaddress
import re
from string import Template

TALARIA_UNITS = ("talaria-check.service", "talaria-check.timer", "talaria-telegram.service")

_ADD_HOST_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}")


def _valid_add_host(h: str) -> bool:
    name, sep, addr = h.partition(":")
    if not sep or not _ADD_HOST_NAME.fullmatch(name):
        return False
    try:
        ipaddress.IPv4Address(addr)
    except ValueError:
        return False
    return True


def _tpl(ctx, name: str) -> Template:
    return Template((ctx.paths.templates_dir / name).read_text())


def render_quadlet(ctx) -> str:
    c = ctx.conf
    if not c.bind_ip:   # an empty address would publish the dashboard on every interface
        raise ValueError(f"dashboard.bind = {c.dashboard_bind} but no address is known")
    wait = ""
    if c.dashboard_bind != "loopback":   # tailscale or a literal address that may appear late
        wait = ("ExecStartPre=/usr/bin/timeout 120 /bin/sh -c "
                f"'until ip -4 -o addr show | grep -qF \" {c.bind_ip}/\"; do sleep 1; done'")
    for h in c.add_hosts:
        if not _valid_add_host(h):
            raise ValueError(f"bad add_hosts entry: {h!r}")
    add_hosts = "".join(f"AddHost={h}\n" for h in c.add_hosts)
    host_net = "Network=slirp4netns:allow_host_loopback=true\n" if c.host_loopback else ""
    return _tpl(ctx, ctx.app.quadlet_file).substitute(
        data_dir=c.data_dir, app_env=ctx.paths.app_env, bind_ip=c.bind_ip,
        port=c.dashboard_port, marker=ctx.paths.marker, wait_addr=wait, host_net=host_net,
        add_hosts=add_hosts, title=ctx.app.title, **ctx.app.quadlet_vars(ctx))


def render_units(ctx) -> dict[str, str]:
    return {name: _tpl(ctx, name).substitute(check_time=ctx.conf.check_time, title=ctx.app.title)
            for name in TALARIA_UNITS}


def _write(path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return True


def install_units(ctx) -> bool:
    changed = _write(ctx.paths.quadlet, render_quadlet(ctx))
    for name, text in render_units(ctx).items():
        _write(ctx.paths.units_dir / name, text)
    ctx.sh.run(["systemctl", "--user", "daemon-reload"])
    return changed

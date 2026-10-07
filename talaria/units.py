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
    ips = c.bind_ips
    if not ips or "" in ips:   # an empty address would publish on every interface
        raise ValueError(f"dashboard.bind = {c.dashboard_bind} but no address is known")
    late = [i for i in ips if i != "127.0.0.1"]   # tailscale or literal: may appear late
    wait = ""
    if late:
        cond = " && ".join(f"ip -4 -o addr show | grep -qF \" {i}/\"" for i in late)
        wait = f"ExecStartPre=/usr/bin/timeout 120 /bin/sh -c 'until {cond}; do sleep 1; done'"
    publish = "".join(f"PublishPort={i}:{c.dashboard_port}:{ctx.app.container_port}\n"
                      for i in ips)
    for h in c.add_hosts:
        if not _valid_add_host(h):
            raise ValueError(f"bad add_hosts entry: {h!r}")
    add_hosts = "".join(f"AddHost={h}\n" for h in c.add_hosts)
    host_net = "Network=slirp4netns:allow_host_loopback=true\n" if c.host_loopback else ""
    return _tpl(ctx, ctx.app.quadlet_file).substitute(
        data_dir=c.data_dir, app_env=ctx.paths.app_env, publish_ports=publish,
        marker=ctx.paths.marker, wait_addr=wait, host_net=host_net,
        add_hosts=add_hosts, title=ctx.app.title, **ctx.app.quadlet_vars(ctx))


HUB_TITLE = "new"     # "Talaria: check for new releases": the hub checks every app


def _render(ctx, title: str) -> dict[str, str]:
    return {name: _tpl(ctx, name).substitute(check_time=ctx.conf.check_time, title=title)
            for name in TALARIA_UNITS}


def render_units(ctx) -> dict[str, str]:
    """The units a v0.4 app install has on disk (pinned by the golden tests)."""
    return _render(ctx, ctx.app.title)


def render_hub_units(ctx) -> dict[str, str]:
    return _render(ctx, HUB_TITLE)


def _write(path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return True


def install_units(ctx) -> bool:
    """An app install gets only its Quadlet; the bot and the timer belong to the hub."""
    changed = _write(ctx.paths.quadlet, render_quadlet(ctx))
    ctx.sh.run(["systemctl", "--user", "daemon-reload"])
    return changed


def install_hub_units(ctx) -> bool:
    changed = False
    for name, text in render_hub_units(ctx).items():
        changed = _write(ctx.paths.units_dir / name, text) or changed
    ctx.sh.run(["systemctl", "--user", "daemon-reload"])
    return changed

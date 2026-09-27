from __future__ import annotations

from string import Template

TALARIA_UNITS = ("talaria-check.service", "talaria-check.timer", "talaria-telegram.service")


def _tpl(ctx, name: str) -> Template:
    return Template((ctx.paths.templates_dir / name).read_text())


def render_quadlet(ctx) -> str:
    c = ctx.conf
    if not c.bind_ip:   # an empty address would publish the dashboard on every interface
        raise ValueError(f"dashboard.bind = {c.dashboard_bind} but no address is known")
    wait = ""
    if c.dashboard_bind == "tailscale":
        wait = ("ExecStartPre=/usr/bin/timeout 120 /bin/sh -c "
                f"'until ip -4 -o addr show | grep -qF \" {c.tailscale_ip}/\"; do sleep 1; done'")
    return _tpl(ctx, "hermes.container").substitute(
        data_dir=c.data_dir, hermes_env=ctx.paths.hermes_env, bind_ip=c.bind_ip,
        port=c.dashboard_port, marker=ctx.paths.marker, wait_tailscale=wait)


def render_units(ctx) -> dict[str, str]:
    return {name: _tpl(ctx, name).substitute(check_time=ctx.conf.check_time)
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

from __future__ import annotations

import json
import re
from pathlib import Path

UNIT = "hermes.service"
_CFG = re.compile(r"^_config_version:\s*(\d+)\s*$", re.M)


def _sc(ctx, *args, check=True):
    return ctx.sh.run(["systemctl", "--user", *args], check=check, timeout=600)


def stop(ctx) -> None:
    _sc(ctx, "stop", UNIT)


def start(ctx) -> None:
    _sc(ctx, "reset-failed", UNIT, check=False)
    _sc(ctx, "start", UNIT)


def is_active(ctx) -> bool:
    return _sc(ctx, "is-active", UNIT, check=False).stdout.strip() == "active"


def nrestarts(ctx) -> int:
    return int(_sc(ctx, "show", "-p", "NRestarts", "--value", UNIT).stdout.strip() or 0)


def api_status(ctx) -> str | None:
    url = f"http://{ctx.conf.bind_ip}:{ctx.conf.dashboard_port}/api/status"
    code, body = ctx.http_get(url, 5.0)
    if code != 200:
        return f"/api/status answered {code or 'nothing'}"
    try:
        if json.loads(body).get("auth_required") is True:
            return None
    except ValueError:
        pass
    return "/api/status does not report auth_required: true"


def post_start_check(ctx) -> str | None:
    base = nrestarts(ctx)
    waited = 0
    while waited < ctx.conf.settle_seconds:
        ctx.sleep(5)
        waited += 5
        if not is_active(ctx):
            return "hermes.service is not active"
        if nrestarts(ctx) != base:
            return "hermes.service restarted"
    reason = None  # pragma: no mutate  (overwritten before it is returned)
    for _ in range(12):
        reason = api_status(ctx)
        if reason is None:
            return None
        ctx.sleep(5)
    return reason


def config_version(data_dir: Path) -> int | None:
    try:
        found = _CFG.findall((Path(data_dir) / "config.yaml").read_text())
    except (FileNotFoundError, UnicodeDecodeError):
        return None
    return int(found[0]) if len(found) == 1 else None

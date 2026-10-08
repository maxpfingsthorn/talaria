"""The hub's settings (spec §3). An install is the hub iff ~/.config/talaria/hub.conf exists."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from talaria.conf import parse_kv, read_telegram_env, unknown_key

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")   # goes into a root shell block
RESERVED = ("hub", "talaria")        # "hub": button prefix of the hub itself; "talaria": /check talaria


@dataclass
class HubConf:
    apps: tuple = ()                     # ((app, user), ...) in hub.conf order
    check_time: str = "04:30"
    talaria_repo: str = "https://github.com/maxpfingsthorn/talaria"
    telegram_api: str = "https://api.telegram.org"
    telegram_token: str = field(default="", repr=False)
    telegram_user_id: int = 0


_KEYS = {"apps": "apps", "check.time": "check_time", "talaria_repo": "talaria_repo",
         "telegram_api": "telegram_api"}


def is_hub(paths) -> bool:
    return paths.hub_conf.exists()


def parse_apps(value: str, where) -> tuple:
    from talaria import apps
    out, names, users = [], set(), set()
    for entry in value.split():
        app, sep, user = entry.partition(":")
        if not sep or app in RESERVED or app not in apps.NAMES:
            raise ValueError(f"apps: {entry!r} is not <app>:<user> with app one of "
                             f"{', '.join(apps.NAMES)} in {where}")
        if not NAME_RE.match(user):
            raise ValueError(f"apps: not a valid account name: {user!r} in {where}")
        if app in names:
            raise ValueError(f"apps: {app} is registered twice in {where}")
        if user in users:   # one account holds one talaria.conf, so one app
            raise ValueError(f"apps: the account {user} is registered twice in {where}")
        names.add(app)
        users.add(user)
        out.append((app, user))
    return tuple(out)


def load_hub_conf(paths, strict: bool = False) -> HubConf:
    conf = HubConf()
    if paths.hub_conf.exists():
        for k, v in parse_kv(paths.hub_conf.read_text()).items():
            if k not in _KEYS:
                unknown_key(paths.hub_conf, k, strict)
                continue
            setattr(conf, _KEYS[k], parse_apps(v, paths.hub_conf) if k == "apps" else v)
    conf.telegram_token, conf.telegram_user_id = read_telegram_env(paths.env_file)
    return conf


def register_app(paths, app: str, user: str) -> bool:
    """Add app:user to the `apps` line, in place, keeping every other line. False if it is
    already registered; ValueError if the app is registered for another account or the
    entry is invalid."""
    current = load_hub_conf(paths, strict=True).apps
    if (app, user) in current:
        return False
    for a, u in current:
        if a == app:
            raise ValueError(f"{paths.hub_conf} already registers {app} for the account {u}")
    value = " ".join(f"{a}:{u}" for a, u in (*current, (app, user)))
    parse_apps(value, paths.hub_conf)
    text = paths.hub_conf.read_text() if paths.hub_conf.exists() else ""
    lines, done = [], False
    for line in text.splitlines():
        if set(parse_kv(line)) == {"apps"}:
            lines.append(f"apps = {value}")
            done = True
        else:
            lines.append(line)
    if not done:
        lines.append(f"apps = {value}")
    paths.hub_conf.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.hub_conf.with_name(paths.hub_conf.name + ".tmp")  # pragma: no mutate  (name of a file that is renamed away)
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, paths.hub_conf)
    return True

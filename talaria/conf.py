from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from pathlib import Path


def parse_kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def read_telegram_env(path: Path) -> tuple[str, int]:
    """(TALARIA_TELEGRAM_TOKEN, TALARIA_TELEGRAM_USER_ID) from an .env file; ("", 0) if absent."""
    if not path.exists():
        return "", 0
    env = parse_kv(path.read_text())
    return (env.get("TALARIA_TELEGRAM_TOKEN", ""),
            int(env.get("TALARIA_TELEGRAM_USER_ID", "0") or 0))


def _bool(v: str) -> bool:
    return v.lower() in ("1", "true", "yes", "on")


@dataclass
class Conf:
    data_dir: Path
    app: str = "hermes"
    image: str = "docker.io/nousresearch/hermes-agent"
    repo: str = "https://github.com/NousResearch/hermes-agent"
    talaria_repo: str = "https://github.com/maxpfingsthorn/talaria"
    dashboard_bind: str = "loopback"
    dashboard_port: int = 9119
    dashboard_public_url: str = ""    # only shown by login-link
    tailscale_ip: str = ""
    host_loopback: bool = False
    backup_keep: int = 5
    backup_exclude: tuple = (".cache", ".npm", "home/.cache", "home/.npm", "backups")
    add_hosts: tuple = ()
    disk_floor_gb: float = 6.0
    check_time: str = "04:30"
    # test-only keys (spec §10)
    registry_tls_verify: bool = True
    min_release: str = "v2026.6.5"
    settle_seconds: int = 60
    telegram_api: str = "https://api.telegram.org"
    release_allow: tuple = ()
    # from .env
    telegram_token: str = field(default="", repr=False)
    telegram_user_id: int = 0

    @property
    def bind_ips(self) -> list[str]:
        """Every published address, in order; "" for a tailscale entry with no known address."""
        return [_resolve(e, self.tailscale_ip) for e in self.dashboard_bind.split()]

    @property
    def bind_ip(self) -> str:
        """The primary (first) address; health checks and status use it."""
        ips = self.bind_ips
        return ips[0] if ips else ""

    @property
    def hermes_repo(self) -> str:
        """Read-only alias of `repo`, kept until all callers use `repo` directly."""
        return self.repo


_KEYS = {
    "data_dir": ("data_dir", "path"), "app": ("app", str), "image": ("image", str),
    "repo": ("repo", str), "hermes_repo": ("repo", str), "talaria_repo": ("talaria_repo", str),
    "dashboard.bind": ("dashboard_bind", str), "dashboard.port": ("dashboard_port", int),
    "dashboard.public_url": ("dashboard_public_url", str),
    "tailscale_ip": ("tailscale_ip", str), "host_loopback": ("host_loopback", "bool"),
    "backup.keep": ("backup_keep", int),
    "backup.exclude": ("backup_exclude", "list"), "add_hosts": ("add_hosts", "list"),
    "disk.floor_gb": ("disk_floor_gb", float),
    "check.time": ("check_time", str), "registry_tls_verify": ("registry_tls_verify", "bool"),
    "min_release": ("min_release", str), "settle_seconds": ("settle_seconds", int),
    "telegram_api": ("telegram_api", str),
    "release_allow": ("release_allow", "list"),
}


def _resolve(entry: str, tailscale_ip: str) -> str:
    if entry == "tailscale":
        return tailscale_ip
    if entry == "loopback":
        return "127.0.0.1"
    return entry  # a literal IPv4 address, checked at load


def check_bind(value: str, where, tailscale_ip: str = "") -> None:
    """dashboard.bind: 1-3 of loopback, tailscale or a private literal IPv4 address."""
    entries = value.split()
    if not 1 <= len(entries) <= 3:
        raise ValueError(f"dashboard.bind needs 1 to 3 entries, got {value!r} in {where}")
    seen = []
    for e in entries:
        ip = None
        if e not in ("loopback", "tailscale"):
            try:
                ip = ipaddress.IPv4Address(e)
            except ValueError:
                pass
            if ip is None or ip.is_unspecified or ip.is_global or ip.is_multicast:
                raise ValueError(f"dashboard.bind entries must be loopback, tailscale or a "
                                 f"private, non-public IPv4 address (not 0.0.0.0), got {e!r} "
                                 f"in {where}")
            if ip.is_loopback and e != "127.0.0.1":
                raise ValueError(f"dashboard.bind: use loopback instead of {e!r} in {where}")
        r = _resolve(e, tailscale_ip)
        if r and r in seen:
            raise ValueError(f"dashboard.bind lists {r} twice in {where}")
        seen.append(r)


def check_public_url(value: str, where) -> str:
    """dashboard.public_url: https://host[:port] or http://127.0.0.1 / http://localhost,
    no path; a trailing slash is dropped. Empty means not set."""
    url = value[:-1] if value.endswith("/") else value
    if url:
        u = urlsplit(url)
        ok = (u.scheme == "https" and u.hostname) or \
             (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"))
        if not ok or u.path or u.query or u.fragment or u.username or u.password \
                or any(c.isspace() for c in url):
            raise ValueError(f"dashboard.public_url must be https://<host> or "
                             f"http://127.0.0.1 / http://localhost, without a path, "
                             f"got {value!r} in {where}")
    return url


def _as_path(paths, v: str) -> Path:
    return paths.home / v[2:] if v.startswith("~/") else Path(v)


def load_conf(paths) -> Conf:
    conf = Conf(data_dir=paths.home / "hermes-data", app=paths.app)
    seen = set()
    if paths.conf_file.exists():
        kv = parse_kv(paths.conf_file.read_text())
        if "repo" in kv and "hermes_repo" in kv:
            raise ValueError(f"set only one of repo / hermes_repo in {paths.conf_file}")
        for k, v in kv.items():
            if k not in _KEYS:
                raise ValueError(f"unknown key in {paths.conf_file}: {k}")
            attr, kind = _KEYS[k]
            if kind == "path":
                val = _as_path(paths, v)
            elif kind == "list":
                val = tuple(v.split())
            elif kind == "bool":
                val = _bool(v)
            else:
                val = kind(v)
            setattr(conf, attr, val)
            seen.add(attr)
    from talaria import apps
    app = apps.get(conf.app)
    check_bind(conf.dashboard_bind, paths.conf_file, conf.tailscale_ip)
    conf.dashboard_public_url = check_public_url(conf.dashboard_public_url, paths.conf_file)
    if paths.conf_file.exists() and conf.backup_keep < 1:
        # retention would delete the undo backup it just made
        raise ValueError(f"backup.keep must be at least 1 in {paths.conf_file}")
    if "data_dir" not in seen:
        conf.data_dir = _as_path(paths, app.default_data_dir)
    if "dashboard_port" not in seen:
        conf.dashboard_port = app.default_port
    if "repo" not in seen:
        conf.repo = app.default_repo
    if "image" not in seen:
        conf.image = app.default_image
    if "min_release" not in seen:
        conf.min_release = app.min_release
    if "backup_exclude" not in seen:
        conf.backup_exclude = app.backup_exclude
    conf.telegram_token, conf.telegram_user_id = read_telegram_env(paths.env_file)
    return conf


def write_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    new, found = [], False
    for l in lines:
        if l.startswith(f"{key}="):
            new.append(f"{key}={value}")
            found = True
        else:
            new.append(l)
    if not found:
        new.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(new) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

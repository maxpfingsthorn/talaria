from __future__ import annotations

import os
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
    tailscale_ip: str = ""
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
    # from .env
    telegram_token: str = field(default="", repr=False)
    telegram_user_id: int = 0

    @property
    def bind_ip(self) -> str:
        return self.tailscale_ip if self.dashboard_bind == "tailscale" else "127.0.0.1"

    @property
    def hermes_repo(self) -> str:
        """Read-only alias of `repo`, kept until all callers use `repo` directly."""
        return self.repo


_KEYS = {
    "data_dir": ("data_dir", "path"), "app": ("app", str), "image": ("image", str),
    "repo": ("repo", str), "hermes_repo": ("repo", str), "talaria_repo": ("talaria_repo", str),
    "dashboard.bind": ("dashboard_bind", str), "dashboard.port": ("dashboard_port", int),
    "tailscale_ip": ("tailscale_ip", str), "backup.keep": ("backup_keep", int),
    "backup.exclude": ("backup_exclude", "list"), "add_hosts": ("add_hosts", "list"),
    "disk.floor_gb": ("disk_floor_gb", float),
    "check.time": ("check_time", str), "registry_tls_verify": ("registry_tls_verify", "bool"),
    "min_release": ("min_release", str), "settle_seconds": ("settle_seconds", int),
    "telegram_api": ("telegram_api", str),
}


def _as_path(paths, v: str) -> Path:
    return paths.home / v[2:] if v.startswith("~/") else Path(v)


def load_conf(paths) -> Conf:
    conf = Conf(data_dir=paths.home / "hermes-data", app=paths.app)
    seen = set()
    if paths.conf_file.exists():
        for k, v in parse_kv(paths.conf_file.read_text()).items():
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
    if paths.env_file.exists():
        env = parse_kv(paths.env_file.read_text())
        conf.telegram_token = env.get("TALARIA_TELEGRAM_TOKEN", "")
        conf.telegram_user_id = int(env.get("TALARIA_TELEGRAM_USER_ID", "0") or 0)
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

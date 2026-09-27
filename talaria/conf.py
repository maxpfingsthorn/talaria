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
    image: str = "docker.io/nousresearch/hermes-agent"
    hermes_repo: str = "https://github.com/NousResearch/hermes-agent"
    talaria_repo: str = "https://github.com/maxpfingsthorn/talaria"
    dashboard_bind: str = "loopback"
    dashboard_port: int = 9119
    tailscale_ip: str = ""
    backup_keep: int = 5
    backup_exclude: tuple = (".cache", ".npm", "home/.cache", "home/.npm", "backups")
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


_KEYS = {
    "data_dir": ("data_dir", "path"), "image": ("image", str),
    "hermes_repo": ("hermes_repo", str), "talaria_repo": ("talaria_repo", str),
    "dashboard.bind": ("dashboard_bind", str), "dashboard.port": ("dashboard_port", int),
    "tailscale_ip": ("tailscale_ip", str), "backup.keep": ("backup_keep", int),
    "backup.exclude": ("backup_exclude", "list"), "disk.floor_gb": ("disk_floor_gb", float),
    "check.time": ("check_time", str), "registry_tls_verify": ("registry_tls_verify", "bool"),
    "min_release": ("min_release", str), "settle_seconds": ("settle_seconds", int),
    "telegram_api": ("telegram_api", str),
}


def load_conf(paths) -> Conf:
    conf = Conf(data_dir=paths.home / "hermes-data")
    if paths.conf_file.exists():
        for k, v in parse_kv(paths.conf_file.read_text()).items():
            if k not in _KEYS:
                raise ValueError(f"unknown key in {paths.conf_file}: {k}")
            attr, kind = _KEYS[k]
            if kind == "path":
                val = paths.home / v[2:] if v.startswith("~/") else Path(v)
            elif kind == "list":
                val = tuple(v.split())
            elif kind == "bool":
                val = _bool(v)
            else:
                val = kind(v)
            setattr(conf, attr, val)
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

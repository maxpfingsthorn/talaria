from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Paths:
    home: Path
    app: str = "hermes"
    helpers_dir = _ROOT / "helpers"
    templates_dir = _ROOT / "templates"

    @property
    def conf_dir(self): return self.home / ".config/talaria"
    @property
    def conf_file(self): return self.conf_dir / "talaria.conf"
    @property
    def env_file(self): return self.conf_dir / ".env"
    @property
    def app_env(self):
        from talaria import apps
        return self.conf_dir / apps.get(self.app).env_file
    @property
    def hermes_env(self): return self.app_env
    @property
    def state_dir(self): return self.home / ".local/state/talaria"
    @property
    def state_file(self): return self.state_dir / "state.json"
    @property
    def lock_file(self): return self.state_dir / "lock"
    @property
    def marker(self): return self.state_dir / "changing"
    @property
    def backups(self): return self.state_dir / "backups"
    @property
    def staging(self): return self.state_dir / "staging"
    @property
    def history(self): return self.state_dir / "history"
    @property
    def install_dir(self): return self.home / ".local/share/talaria"
    @property
    def bin_link(self): return self.home / ".local/bin/talaria"
    @property
    def quadlet_dir(self): return self.home / ".config/containers/systemd"
    @property
    def quadlet(self):
        from talaria import apps
        return self.quadlet_dir / apps.get(self.app).quadlet_file
    @property
    def units_dir(self): return self.home / ".config/systemd/user"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def http_get(url: str, timeout: float = 5.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, OSError):
        return 0, b""


def download(url: str, dest: Path, max_bytes: int) -> int:
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
            n = 0
            while chunk := r.read(1 << 20):
                n += len(chunk)
                if n > max_bytes:
                    raise OSError(f"{url} is larger than {max_bytes} bytes")
                f.write(chunk)
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError):
        return 0


@dataclass
class Ctx:
    paths: Paths
    conf: object
    sh: object
    notify: object
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = _utcnow
    http_get: Callable[[str, float], tuple[int, bytes]] = http_get
    download: Callable[[str, Path, int], int] = download
    app: object = None


def make_ctx() -> Ctx:
    from talaria import apps
    from talaria.conf import load_conf
    from talaria.notify import TelegramNotifier
    from talaria.shell import Shell

    conf = load_conf(Paths(Path.home()))
    paths = Paths(Path.home(), conf.app)
    return Ctx(paths=paths, conf=conf, sh=Shell(), notify=TelegramNotifier(conf),
               app=apps.get(conf.app))

from __future__ import annotations

import http.client
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
    def hub_conf(self): return self.conf_dir / "hub.conf"
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
    def hub_state(self): return self.state_dir / "hub.json"
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


def local_now(ctx) -> datetime:
    """ctx.now() in the host's local time zone (systemd timers fire in local time)."""
    return ctx.now().astimezone()


def http_get(url: str, timeout: float = 5.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, OSError):
        return 0, b""


class TooLarge(OSError):
    """Raised by download() only when the response exceeds max_bytes. Every other
    failure while streaming the body (a mid-stream reset, a TLS error, disk full,
    an incomplete/chunked-encoding error) is swallowed into a 0 return instead, the
    same as a non-200 response: those are transient, not a bad publish."""


def download(url: str, dest: Path, max_bytes: int) -> int:
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
            n = 0
            while chunk := r.read(1 << 20):
                n += len(chunk)
                if n > max_bytes:
                    raise TooLarge(f"{url} is larger than {max_bytes} bytes")
                f.write(chunk)
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except TooLarge:
        raise
    except (urllib.error.URLError, http.client.HTTPException, OSError):
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


def make_ctx(app: str | None = None, strict: bool = False) -> Ctx:
    """`app` seeds the app to use when talaria.conf does not exist yet (a fresh install
    picked with `talaria setup --app ...`). An existing conf file is read as it is, so a
    conf without an `app` key stays `hermes` and setup can catch a contradicting --app."""
    from talaria import apps
    from talaria.conf import load_conf
    from talaria.notify import TelegramNotifier
    from talaria.shell import Shell

    seed = Paths(Path.home())
    if app and not seed.conf_file.exists():
        seed = Paths(Path.home(), app)
    conf = load_conf(seed, strict)
    paths = Paths(Path.home(), conf.app)
    return Ctx(paths=paths, conf=conf, sh=Shell(), notify=TelegramNotifier(conf),
               app=apps.get(conf.app))


def make_hub_ctx(home: Path | None = None, sh=None, strict: bool = False) -> Ctx:
    """The hub account's ctx: hub.conf instead of talaria.conf, no app."""
    from talaria.hubconf import load_hub_conf
    from talaria.notify import TelegramNotifier
    from talaria.shell import Shell

    paths = Paths(Path(home) if home else Path.home())
    conf = load_hub_conf(paths, strict)
    return Ctx(paths=paths, conf=conf, sh=sh or Shell(), notify=TelegramNotifier(conf))

"""How the hub reaches an app: `talaria op …` as the app's account (spec §4, §5.3)."""
from __future__ import annotations

import json
import pwd
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from talaria import apps


class Unreachable(Exception):
    """sudo refused: the hub's op rule for this app is missing."""


class NoAnswer(Exception):
    """A quick op did not finish in time."""


def parse_lines(text: str, app: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            d = None  # pragma: no mutate  (any non-dict falls to the same branch)
        if isinstance(d, dict) and d.get("v") == 1 and isinstance(d.get("kind"), str):
            out.append(d)
        else:
            print(f"[talaria] {app}: ignored output: {line[:200]}", file=sys.stderr)
    return out


class _Executor:
    app = ""
    returncode: int | None = None

    def argv(self, op_argv) -> list[str]:
        raise NotImplementedError

    def _refused(self, rc, err: str) -> bool:
        return False

    def call(self, op_argv, timeout: float = 60) -> list[dict]:
        """A quick op: wait for it (up to `timeout` seconds), return its lines."""
        self.returncode = None
        try:
            p = subprocess.run(self.argv(op_argv), capture_output=True, text=True,
                               timeout=timeout, cwd="/")
        except subprocess.TimeoutExpired:
            raise NoAnswer(self.app) from None
        self.returncode = p.returncode
        if p.stderr:
            sys.stderr.write(p.stderr)
        if self._refused(p.returncode, p.stderr):
            raise Unreachable(self.app)
        return parse_lines(p.stdout, self.app)

    def stream(self, op_argv) -> Iterator[dict]:
        """A long op: yield each line as it arrives. stderr goes to a file, never a pipe,
        so a chatty op cannot block on it while we read stdout."""
        self.returncode = None
        with tempfile.TemporaryFile(mode="w+") as err:
            p = subprocess.Popen(self.argv(op_argv), stdout=subprocess.PIPE, stderr=err,
                                 text=True, cwd="/")
            try:
                for line in p.stdout:
                    yield from parse_lines(line, self.app)
            finally:
                p.stdout.close()
                self.returncode = p.wait()
                err.seek(0)
                text = err.read()
                if text:
                    sys.stderr.write(text)
        if self._refused(self.returncode, text):
            raise Unreachable(self.app)


class SudoExecutor(_Executor):
    """The hub's way in: one sudo rule per app allows exactly `<home>/.local/bin/talaria op *`."""

    def __init__(self, app: str, user: str, home, sudo: str = "sudo"):
        self.app, self.user, self.home, self.sudo = app, user, str(home), sudo

    def argv(self, op_argv) -> list[str]:
        return [self.sudo, "-n", "-H", "-u", self.user, f"{self.home}/.local/bin/talaria",
                "op", *op_argv]

    def _refused(self, rc, err: str) -> bool:
        return rc == 1 and err.lstrip().startswith("sudo:")


class LocalExecutor(_Executor):
    """Transitional mode (spec §7.5): a v0.4 app account still runs its own bot, so op
    runs as the same account, without sudo."""

    def __init__(self, app: str, bin_link):
        self.app, self.bin = app, str(bin_link)

    def argv(self, op_argv) -> list[str]:
        return [self.bin, "op", *op_argv]


@dataclass(frozen=True)
class AppEntry:
    name: str
    title: str
    executor: object


@dataclass
class Hub:
    ctx: object                       # this account's Ctx: telegram_*, check_time, talaria_repo
    apps: dict = field(default_factory=dict)   # name -> AppEntry, in hub.conf order
    transitional: bool = False


def load_hub(home: Path | None = None, *, getpwnam=pwd.getpwnam, sh=None) -> Hub | None:
    """This account as a hub: a real hub (hub.conf exists), a v0.4-layout app install that
    still holds its own bot token and owner (transitional), or None (an app under a hub)."""
    from talaria.conf import load_conf
    from talaria.ctx import Ctx, Paths, make_hub_ctx
    from talaria.notify import TelegramNotifier
    from talaria.shell import Shell

    home = Path(home) if home else Path.home()
    paths = Paths(home)
    if paths.hub_conf.exists():
        ctx = make_hub_ctx(home, sh=sh)
        reg = {}
        for app, user in ctx.conf.apps:
            try:
                pw = getpwnam(user)
            except KeyError:
                raise ValueError(f"{paths.hub_conf} registers {app} for {user}, but that "
                                 "account does not exist") from None
            reg[app] = AppEntry(app, apps.get(app).title, SudoExecutor(app, user, pw.pw_dir))
        return Hub(ctx, reg)
    if not paths.conf_file.exists():
        return None
    conf = load_conf(paths)
    if not conf.telegram_token or not conf.telegram_user_id:
        return None
    a = apps.get(conf.app)
    paths = Paths(home, conf.app)
    ctx = Ctx(paths=paths, conf=conf, sh=sh or Shell(), notify=TelegramNotifier(conf), app=a)
    return Hub(ctx, {a.name: AppEntry(a.name, a.title, LocalExecutor(a.name, paths.bin_link))},
               transitional=True)

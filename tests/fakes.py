from __future__ import annotations

from datetime import datetime, timezone

from talaria.conf import load_conf
from talaria.ctx import Ctx, Paths
from talaria.shell import CommandError, Result


class FakeShell:
    def __init__(self):
        self.rules = []
        self.calls: list[list[str]] = []
        self.timeouts: list = []          # parallel to calls

    def on(self, *prefix, out="", rc=0, err="", fn=None):
        """Most recently added matching rule wins. fn(argv, input) -> Result."""
        handler = fn or (lambda argv, input: Result(rc, out, err))
        self.rules.insert(0, (tuple(str(p) for p in prefix), handler))
        return self

    def run(self, argv, *, input=None, check=True, timeout=None):
        assert isinstance(check, bool), f"check must be a bool, got {check!r}"
        assert timeout is None or (isinstance(timeout, (int, float)) and timeout > 0), timeout
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        self.timeouts.append(timeout)
        for prefix, handler in self.rules:
            if tuple(argv[:len(prefix)]) == prefix:
                r = handler(argv, input)
                if check and r.returncode != 0:
                    raise CommandError(argv, r)
                return r
        raise AssertionError(f"unexpected command: {argv}")

    def called(self, *prefix):
        prefix = tuple(str(p) for p in prefix)
        return [c for c in self.calls if tuple(c[:len(prefix)]) == prefix]


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)

    def texts(self):
        return [m.text for m in self.sent]


class Clock:
    def __init__(self):
        self.t = datetime(2026, 9, 27, 4, 30, tzinfo=timezone.utc)
        self.slept = 0.0

    def now(self):
        from datetime import timedelta
        return self.t + timedelta(seconds=self.slept)

    def sleep(self, s):
        self.slept += s


def make_test_ctx(tmp_path, **overrides) -> Ctx:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    paths = Paths(home)
    conf = load_conf(paths)
    conf.disk_floor_gb = 0          # tests must not depend on the real disk's free space
    for k, v in overrides.items():
        setattr(conf, k, v)
    conf.data_dir.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    ctx = Ctx(paths=paths, conf=conf, sh=FakeShell(), notify=FakeNotifier(),
              sleep=clock.sleep, now=clock.now,
              http_get=lambda url, timeout=5.0: (200, b'{"auth_required": true}'))
    ctx.clock = clock
    return ctx

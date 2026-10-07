# tests/hubfakes.py
"""Fakes for the hub side: an executor answering `op` calls from rules, and a Hub."""
from talaria import apps
from talaria.hubexec import AppEntry, Hub
from tests.fakes import make_test_ctx


def line(kind, **fields):
    return {"v": 1, "kind": kind, **fields}


def _rows(buttons):
    return [[list(b) for b in row] for row in buttons]


def reply(text, buttons=()):
    return line("reply", text=text, buttons=_rows(buttons))


def message(text, blocks=(), commands=(), buttons=()):
    return line("message", text=text, blocks=list(blocks), commands=list(commands),
                buttons=_rows(buttons))


def hello(protocol=1, app="hermes"):
    return line("hello", protocol=protocol, app=app, title=apps.get(app).title,
                version="0.5.0")


class FakeExecutor:
    """Stands in for SudoExecutor/LocalExecutor. Rules match an op argv prefix; the most
    recently added matching rule wins. `exc` is raised before any line by call() and
    after the lines by stream(), like the real executors."""

    def __init__(self, app="hermes", log=None):
        self.app, self.rules, self.calls, self.returncode, self.log = app, [], [], None, log

    def on(self, *prefix, lines=(), rc=0, exc=None):
        self.rules.insert(0, (tuple(prefix), list(lines), rc, exc))
        return self

    def _rule(self, argv):
        for prefix, lines, rc, exc in self.rules:
            if tuple(argv[:len(prefix)]) == prefix:
                return lines, rc, exc
        raise AssertionError(f"unexpected op for {self.app}: {argv}")

    def _note(self, how, argv, timeout):
        self.calls.append((how, list(argv), timeout))
        if self.log is not None:
            self.log.append((self.app, how, tuple(argv)))

    def call(self, argv, timeout=60):
        self._note("call", argv, timeout)
        lines, rc, exc = self._rule(list(argv))
        if exc:
            raise exc
        self.returncode = rc
        return list(lines)

    def stream(self, argv):
        self._note("stream", argv, None)
        lines, rc, exc = self._rule(list(argv))
        yield from lines
        self.returncode = rc
        if exc:
            raise exc

    def ops(self):
        return [argv for _, argv, _ in self.calls]


def make_hub(tmp_path, names=("hermes",), transitional=False, log=None):
    ctx = make_test_ctx(tmp_path, telegram_user_id=42, telegram_token="t")
    entries = {n: AppEntry(n, apps.get(n).title, FakeExecutor(n, log)) for n in names}
    return Hub(ctx=ctx, apps=entries, transitional=transitional)


def ex(hub, name):
    return hub.apps[name].executor

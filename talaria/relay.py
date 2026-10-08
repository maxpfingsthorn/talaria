"""Turns `talaria op` output into Telegram content for the hub (spec §4.3, §5.3)."""
from __future__ import annotations

import sys
import traceback

from talaria.hubexec import NoAnswer, Unreachable
from talaria.notify import Message
from talaria.op import PROTOCOL

VERSIONS = "Talaria versions differ on this host; run /update"


def unreachable_text(title: str) -> str:
    return f"{title}: Talaria cannot reach this app (sudo rule missing); run setup again"


def no_answer_text(title: str) -> str:
    return f"{title} did not answer in time"


def prefix(app: str, rows) -> list:
    """Callback data names its app, so the hub knows where a tap goes (spec §5.2)."""
    return [[(str(label), f"{app}|{data}") for label, data in row] for row in rows or []]


def with_app(app: str, commands) -> list[str]:
    """`/approve v1` → `/approve hermes v1`: typed commands work with several apps."""
    out = []
    for c in commands or []:
        head, sep, rest = str(c).partition(" ")
        out.append(f"{head} {app}{sep}{rest}" if head.startswith("/") else str(c))
    return out


def _block(b):
    if isinstance(b, list) and len(b) == 2:
        return (str(b[0]), str(b[1]))
    return str(b)


def to_message(app: str, d: dict) -> Message:
    return Message(str(d.get("text") or ""), untrusted=[_block(b) for b in d.get("blocks") or []],
                   commands=with_app(app, d.get("commands")), buttons=prefix(app, d.get("buttons")))


def quick(entry, argv: list[str], timeout: float = 60) -> tuple[str, list]:
    try:
        lines = entry.executor.call(argv, timeout=timeout)
    except Unreachable:
        return unreachable_text(entry.title), []
    except NoAnswer:
        return no_answer_text(entry.title), []
    for d in lines:
        if d.get("kind") == "reply":
            return str(d.get("text") or ""), prefix(entry.name, d.get("buttons"))
    rc = entry.executor.returncode
    if rc:
        return f"{entry.title}: {argv[0]} failed unexpectedly (exit {rc})", []
    return "", []


def hello(entry) -> str | None:
    try:
        lines = entry.executor.call(["hello"])
    except Unreachable:
        return unreachable_text(entry.title)
    except NoAnswer:
        return no_answer_text(entry.title)
    if any(d.get("kind") == "hello" and d.get("protocol") == PROTOCOL for d in lines):
        return None
    return VERSIONS


def status_all(hub, mismatch=frozenset()) -> str:
    blocks = [f"{e.title}: {VERSIONS}" if name in mismatch else quick(e, ["status"])[0]
              for name, e in hub.apps.items()]
    return "\n\n".join(b for b in blocks if b) or "No apps registered."


def relay(hub, app: str, argv: list[str]) -> int:
    return relay_sent(hub, app, argv)[0]


def relay_sent(hub, app: str, argv: list[str]) -> tuple[int, bool]:
    """(exit code, whether the app sent any message)."""
    e = hub.apps[app]
    sent = False  # pragma: no mutate  (None is falsy too)
    try:
        for d in e.executor.stream(argv):
            if d.get("kind") == "message":
                hub.ctx.notify.send(to_message(app, d))
                sent = True
    except Unreachable:
        hub.ctx.notify.send(Message(unreachable_text(e.title)))
        return 1, sent
    except Exception as x:    # a missing binary, an odd message shape: never end silently
        traceback.print_exc(file=sys.stderr)  # pragma: no mutate  (stderr is the default)
        hub.ctx.notify.send(Message(f"{e.title}: {' '.join(argv)} — Talaria could not follow "
                                    f"this operation ({x}); details in the journal"))
        return 1, sent
    rc = e.executor.returncode or 0
    if rc != 0 and not sent:
        hub.ctx.notify.send(Message(f"{e.title}: {' '.join(argv)} failed unexpectedly (exit {rc})"))
    return rc, sent


def main(hub, app: str, argv: list[str]) -> int:
    if app not in hub.apps:
        print(f"talaria relay: unknown app {app!r}; apps: {', '.join(hub.apps)}", file=sys.stderr)
        return 2
    if not argv:
        print("talaria relay: no operation given", file=sys.stderr)
        return 2
    return relay(hub, app, argv)

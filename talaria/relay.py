"""Turns `talaria op` output into Telegram content for the hub (spec §4.3, §5.3)."""
from __future__ import annotations

import sys
import traceback

from talaria import __version__
from talaria.hubexec import NoAnswer, Unreachable
from talaria.notify import Message
from talaria.op import PROTOCOL

VERSIONS = "Talaria versions differ on this host; run /update"
SKEW = "Talaria on this app is not at the hub's version; /update retries it"   # a note, nothing blocks


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


def failed_text(entry, what: str) -> str:
    """`<Title>: <what> failed unexpectedly (exit N): <last stderr line>`."""
    line = getattr(entry.executor, "stderr_line", "")
    return (f"{entry.title}: {what} failed unexpectedly (exit {entry.executor.returncode})"
            + (f": {line}" if line else ""))


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
        return failed_text(entry, argv[0]), []
    return "", []


def hello(entry) -> str | None:
    try:
        lines = entry.executor.call(["hello"])
    except Unreachable:
        return unreachable_text(entry.title)
    except NoAnswer:
        return no_answer_text(entry.title)
    h = next((d for d in lines if d.get("kind") == "hello" and d.get("protocol") == PROTOCOL), None)
    if h is not None:
        v = h.get("version")  # absent before v0.5: protocol is all there is to compare
        return SKEW if isinstance(v, str) and v != __version__ else None
    if entry.executor.returncode and getattr(entry.executor, "stderr_line", ""):
        return f"{VERSIONS} ({failed_text(entry, 'hello')})"
    return VERSIONS


def version(entry) -> str | None:
    """The app's Talaria version from hello; None when unknown."""
    try:
        lines = entry.executor.call(["hello"])
    except (Unreachable, NoAnswer):
        return None
    h = next((d for d in lines if d.get("kind") == "hello"), None)
    v = h.get("version") if h else None
    return v if isinstance(v, str) else None


def login(entry, oauth_request: str | None = None) -> tuple[str, bool]:
    """(text, is_link). The text may hold a login link: callers never print or log it."""
    argv = ["login-link"] + (["--oauth-request", oauth_request] if oauth_request else [])
    try:
        lines = entry.executor.call(argv, timeout=60)
    except Unreachable:
        return unreachable_text(entry.title), False
    except NoAnswer:
        return no_answer_text(entry.title), False
    for d in lines:
        if d.get("kind") == "link":
            return str(d.get("text") or ""), True
        if d.get("kind") == "reply":
            return str(d.get("text") or ""), False
    return failed_text(entry, "login-link"), False


def lagging(entry) -> bool:
    """The app answers, but not with this hub's version (unreachable apps are not "lagging")."""
    return (hello(entry) or "").startswith((VERSIONS, SKEW))


def with_skew(text: str) -> str:
    return f"{text}\n({SKEW})"


def status_all(hub, mismatch=frozenset(), skewed=frozenset()) -> str:
    blocks = [f"{e.title}: {VERSIONS}" if name in mismatch else
              with_skew(quick(e, ["status"])[0]) if name in skewed else quick(e, ["status"])[0]
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
        hub.ctx.notify.send(Message(failed_text(e, ' '.join(argv))))
    return rc, sent


def main(hub, app: str, argv: list[str]) -> int:
    if app not in hub.apps:
        print(f"talaria relay: unknown app {app!r}; apps: {', '.join(hub.apps)}", file=sys.stderr)
        return 2
    if not argv:
        print("talaria relay: no operation given", file=sys.stderr)
        return 2
    return relay(hub, app, argv)

"""`talaria op`: the only door from the hub into an app install (spec §4).

Runs as the app's account (through `sudo -n -u <app>` from the hub, or directly in
transitional mode). Parses its own arguments strictly; writes JSON lines to stdout and
everything else to stderr (the journal)."""
from __future__ import annotations

import argparse
import contextlib
import json
import sys

from talaria import __version__, cli, rollback, selfupdate, state, status, units
from talaria.backup import ID_RE
from talaria.notify import Message
from talaria.shell import CommandError
from talaria.tags import semver_newer

PROTOCOL = 1
OPS = ("status", "backups", "check", "deploy", "reject", "rollback", "restore", "button",
       "hello", "interrupted", "self-update", "quadlet")
STALE = "Out of date — send /status"


def emit(out, kind: str, **fields) -> None:
    """One JSON line. If nobody reads the pipe any more (the hub's relay died), the
    operation still finishes; the journal has the details."""
    try:
        out.write(json.dumps({"v": 1, "kind": kind, **fields}) + "\n")
        out.flush()
    except OSError as e:          # BrokenPipeError is one
        print(f"[talaria] output closed ({e}); carrying on", file=sys.stderr)


def _rows(buttons) -> list:
    return [[[str(label), str(data)] for label, data in row] for row in buttons or []]


class JsonNotifier:
    """ctx.notify inside op: each message becomes one `message` line; the hub sends it."""

    def __init__(self, out):
        self.out = out

    def send(self, m: Message) -> None:
        print(f"[talaria] message: {m.text}", file=sys.stderr)
        emit(self.out, "message", text=m.text,
             blocks=[list(b) if isinstance(b, tuple) else b for b in m.untrusted],
             commands=list(m.commands), buttons=_rows(m.buttons))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="talaria op", add_help=False, allow_abbrev=False)
    sub = p.add_subparsers(dest="op", required=True)

    def add(name):
        return sub.add_parser(name, add_help=False, allow_abbrev=False)

    for name in ("status", "backups", "hello", "interrupted"):
        add(name)
    add("check").add_argument("--timer", action="store_true")
    for name in ("deploy", "reject"):
        add(name).add_argument("tag", type=cli._release)
    add("rollback").add_argument("mode", choices=("describe", "confirm"))
    r = add("restore")
    r.add_argument("id", type=cli._backup_id)
    r.add_argument("mode", choices=("describe", "confirm"))
    add("button").add_argument("data")
    s = add("self-update")              # must stay accepted by every later version (§4.2)
    s.add_argument("tag", type=cli._semver)
    s.add_argument("--dry-run", action="store_true")
    add("quadlet")                      # read-only; used by `self-update --dry-run`
    return p


def decide_button(ctx, data) -> tuple[str, str | None, list[str] | None]:
    """(toast, status label, op argv to run). The label replaces the buttons in place; None
    keeps them. Each button names what it acts on and is checked against the current
    state, so an old button never acts on a different target. Rollback and restore
    buttons carry the minute they were sent and expire after an hour."""
    kind, _, arg = (data or "").partition(":")
    if kind == "done":
        return "Already handled", None, None
    st = state.load(ctx.paths)
    if kind in ("ap", "rj") and ctx.app.is_release(arg):
        if kind == "rj":
            if cli.reject(ctx, arg).startswith("Busy"):
                return "Busy, try again in a minute", None, None
            return "Rejected", f"❌ Rejected {arg}", None
        if (st.get("pending") or {}).get("tag") != arg:
            return STALE, "⌛ Out of date", None
        return "Deploying", f"✅ Approved — deploying {arg}", ["deploy", arg]
    bid, _, minute = arg.partition(":")
    if kind == "rb" and (arg == "resume" or ID_RE.match(bid)):
        resume = rollback.needs_resume(ctx, st)
        t = None if resume else rollback.target(ctx, st)
        if (arg == "resume" and resume) or (t and t[0] == bid and rollback.fresh(ctx, minute)):
            return "Rolling back", "↩️ Rolling back…", ["rollback", "confirm"]
        return STALE, "⌛ Out of date", None
    if kind == "rs" and ID_RE.match(bid):
        if not rollback.describe_restore_buttons(ctx, bid) or not rollback.fresh(ctx, minute):
            return STALE, "⌛ Out of date", None
        return "Restoring", f"↩️ Restoring {bid}…", ["restore", bid, "confirm"]
    return "Unknown button", None, None


def _reply(out, text: str, buttons=()) -> int:
    emit(out, "reply", text=text, buttons=_rows(buttons))
    return 0


FIRST_HUB_RELEASE = "v0.5.0"          # the first release that speaks this protocol


def _self_update(ctx, args, out) -> int:
    if semver_newer(FIRST_HUB_RELEASE, args.tag):
        _reply(out, f"refused: {args.tag} is older than {FIRST_HUB_RELEASE}, the first hub "
                    "release; it has no hub door")
        return 2
    if args.dry_run:
        try:
            restart = selfupdate.dry_run(ctx, args.tag)
        except (CommandError, ValueError) as e:
            return _reply(out, f"dry run failed: {e}") or 1
        emit(out, "reply", text=f"would restart: {'yes' if restart else 'no'}", buttons=[],
             restart=restart)
        return 0
    rc = selfupdate.self_update(ctx, args.tag)
    texts = {0: f"Talaria {args.tag} installed.", cli.EXIT_BUSY: "busy: an operation is running"}
    _reply(out, texts.get(rc, f"self-update failed (exit {rc}); details in the journal"))
    return rc


def _run(ctx, args, out) -> int:
    o = args.op
    if o in ("deploy", "reject") and not ctx.app.is_release(args.tag):
        print(f"talaria op: {args.tag} is not a {ctx.app.title} release tag", file=sys.stderr)
        return 2
    if o == "hello":
        emit(out, "hello", protocol=PROTOCOL, app=ctx.app.name, title=ctx.app.title,
             version=__version__)
        return 0
    if o == "quadlet":
        return _reply(out, units.render_quadlet(ctx))
    if o == "self-update":
        return _self_update(ctx, args, out)
    if o == "status":
        return _reply(out, status.status_text(ctx))
    if o == "backups":
        return _reply(out, status.backups_text(ctx))
    if o == "interrupted":
        text = status.interrupted_text(ctx, state.load(ctx.paths))
        return _reply(out, text) if text else 0
    if o == "reject":
        return _reply(out, cli.reject(ctx, args.tag))
    if o == "button":
        toast, label, run = decide_button(ctx, args.data)
        emit(out, "button", toast=toast, status=label, run=run)
        return 0
    if o == "rollback" and args.mode == "describe":
        return _reply(out, rollback.describe(ctx), rollback.describe_buttons(ctx))
    if o == "restore" and args.mode == "describe":
        return _reply(out, rollback.describe_restore(ctx, args.id),
                      rollback.describe_restore_buttons(ctx, args.id))
    if o == "check":
        ns = argparse.Namespace(cmd="check", timer=args.timer)
    elif o == "deploy":
        ns = argparse.Namespace(cmd="deploy", tag=args.tag, timer=False)
    elif o == "rollback":
        ns = argparse.Namespace(cmd="rollback", confirm=True, timer=False)
    else:
        ns = argparse.Namespace(cmd="restore", id=args.id, confirm=True, timer=False)
    return cli.run_locked(ctx, ns)


def main(argv: list[str], make=None, out=None) -> int:
    out = out or sys.stdout          # the JSON channel; everything else goes to stderr
    if not argv or argv[0] not in OPS:
        print(f"talaria op: not allowed: {' '.join(argv)[:80]!r}", file=sys.stderr)
        return 2
    try:
        args = build_parser().parse_args(argv)
    except SystemExit:
        return 2
    with contextlib.redirect_stdout(sys.stderr):
        if make is None:
            from talaria.ctx import make_ctx as make
        ctx = make()
        ctx.notify = JsonNotifier(out)
        return _run(ctx, args, out)

from __future__ import annotations

import argparse
import os
import sys
import traceback

from talaria import (__version__, backup, check, deploy, hermes, history, lock, rollback,
                     state, status)
from talaria.backup import ID_RE
from talaria.ctx import make_ctx
from talaria.notify import Message
from talaria.tags import SEMVER, TAG_ARG

EXIT_BUSY = 75


def _release(v: str) -> str:
    if not TAG_ARG.match(v):
        raise argparse.ArgumentTypeError(f"not a release tag: {v!r}")
    return v


def _backup_id(v: str) -> str:
    if not ID_RE.match(v):
        raise argparse.ArgumentTypeError(f"not a backup id: {v!r}")
    return v


def _semver(v: str) -> str:
    if not SEMVER.match(v):
        raise argparse.ArgumentTypeError(f"not a Talaria release: {v!r}")
    return v


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="talaria")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("version")
    s = sub.add_parser("setup")
    s.add_argument("--plan", action="store_true")
    s.add_argument("--user")
    s.add_argument("--adopt", metavar="UNIT")
    s.add_argument("--dev", action="store_true")
    s.add_argument("--as-service", action="store_true", help=argparse.SUPPRESS)
    sub.add_parser("set-token")
    c = sub.add_parser("check")
    c.add_argument("--timer", action="store_true")
    for name in ("rehearse", "deploy", "reject"):
        sub.add_parser(name).add_argument("tag", type=_release)
    r = sub.add_parser("rollback")
    r.add_argument("--confirm", action="store_true")
    r = sub.add_parser("restore")
    r.add_argument("id", type=_backup_id)
    r.add_argument("--confirm", action="store_true")
    for name in ("backup", "backups", "status", "history", "bot"):
        sub.add_parser(name)
    sub.add_parser("self-update").add_argument("tag", type=_semver)
    return p


def reject(ctx, tag: str) -> str:
    try:
        with lock.op_lock(ctx.paths):
            st = state.load(ctx.paths)
            if tag not in st["rejected"]:
                st["rejected"].append(tag)
            if (st.get("pending") or {}).get("tag") == tag:
                st["pending"] = None
            state.save(ctx.paths, st)
    except lock.Busy:
        return "Busy: another operation is running. Try again in a minute."
    return f"Rejected {tag}. It will not be offered again."


def _manual_backup(ctx) -> None:
    st = state.load(ctx.paths)
    hermes.stop(ctx)
    try:
        b = backup.create(ctx, "manual", st.get("current"))
    finally:
        hermes.start(ctx)
    print(b.id)


def _locked(ctx, args) -> None:
    cmd = args.cmd
    if cmd == "check" or cmd == "rehearse" or cmd == "history":
        st = state.load(ctx.paths)
        try:
            if cmd == "check":
                check.check(ctx, st)
            elif cmd == "rehearse":
                check.rehearse_tag(ctx, st, args.tag)
            else:
                history.commit(ctx, st, "manual")
        finally:
            state.save(ctx.paths, st)     # keep reminders and counters even after a crash
    elif cmd == "deploy":
        deploy.deploy(ctx, args.tag)
    elif cmd == "rollback":
        rollback.rollback_cmd(ctx)
    elif cmd == "restore":
        rollback.restore_cmd(ctx, args.id)
    elif cmd == "backup":
        _manual_backup(ctx)


def main(argv: list[str] | None = None, make=make_ctx) -> int:
    args = build_parser().parse_args(argv)
    # `sudo -u hermes talaria …` has no XDG_RUNTIME_DIR, which systemctl --user needs
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if args.cmd == "version":
        print(__version__)
        return 0
    if args.cmd == "setup":
        from talaria import setup
        return setup.setup(args)
    ctx = make()
    if args.cmd in ("deploy", "rehearse", "reject") and not ctx.app.is_release(args.tag):
        print(f"{args.tag} is not a {ctx.app.title} release tag", file=sys.stderr)
        return 2
    if args.cmd == "set-token":
        from talaria import setup
        return setup.set_token(ctx)
    if args.cmd == "bot":
        from talaria import telegram
        return telegram.run(ctx)
    if args.cmd == "self-update":     # not under the lock: it runs `setup`, which takes it
        from talaria import selfupdate
        return selfupdate.self_update(ctx, args.tag)
    if args.cmd == "status":
        print(status.status_text(ctx))
        return 0
    if args.cmd == "backups":
        print(status.backups_text(ctx))
        return 0
    if args.cmd == "reject":
        print(reject(ctx, args.tag))
        return 0
    if args.cmd == "rollback" and not args.confirm:
        print(rollback.describe(ctx))
        return 0
    if args.cmd == "restore" and not args.confirm:
        print(rollback.describe_restore(ctx, args.id))
        return 0
    try:
        with lock.op_lock(ctx.paths):
            _locked(ctx, args)
    except lock.Busy:
        if args.cmd == "check" and args.timer:
            return 0
        ctx.notify.send(Message("Busy: another operation is running. Try again in a minute."))
        return EXIT_BUSY
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        ctx.notify.send(Message(f"talaria {args.cmd} failed unexpectedly: {e}"))
        return 1
    return 0

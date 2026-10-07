from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import traceback

from talaria import (__version__, backup, check, deploy, history, lock, rollback,
                     service, state, status)
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
    s.add_argument("--app", help="hermes (default) or clawvisor")
    s.add_argument("--as-service", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--hub", default="talaria", help="the hub's account (default talaria)")
    s.add_argument("--as-hub", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--lock-held", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--no-restart", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--register", metavar="APP:USER", help=argparse.SUPPRESS)
    s.add_argument("--import-telegram", action="store_true", help=argparse.SUPPRESS)
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
    for name in ("backup", "backups", "status", "history", "bot", "login-link"):
        sub.add_parser(name)
    sub.add_parser("self-update").add_argument("tag", type=_semver)
    r = sub.add_parser("relay")                  # hub only: run a long op, send its messages
    r.add_argument("app")
    r.add_argument("op", nargs=argparse.REMAINDER)
    u = sub.add_parser("update")                 # hub only
    u.add_argument("tag", type=_semver)
    u.add_argument("--offer", action="store_true")
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


def login_link(ctx) -> int:
    if ctx.app.name != "clawvisor":
        print(f"not available for {ctx.app.title}", file=sys.stderr)
        return 1
    if not sys.stdout.isatty():
        print("run this in your own terminal", file=sys.stderr)
        return 1
    try:
        r = ctx.sh.run(["podman", "exec", ctx.app.container, "/clawvisor-server",
                        "dashboard", "--no-open"], check=False, timeout=30)
    except subprocess.TimeoutExpired:
        print("STOP: podman exec timed out", file=sys.stderr)
        return 1
    if r.returncode != 0:
        print("STOP: could not get a login link from Clawvisor", file=sys.stderr)
        return 1
    # Clawvisor prints its own container-internal port (25297); the Quadlet publishes
    # that on ctx.conf.dashboard_port, which can differ -- rewrite both host and port.
    # One line per published address; the token appears only in these lines.
    port = ctx.conf.dashboard_port
    for ip in ctx.conf.bind_ips:
        print(re.sub(r"http://localhost:\d+", f"http://{ip}:{port}", r.stdout.strip()))
    ts = ctx.conf.tailscale_ip if "tailscale" in ctx.conf.dashboard_bind.split() else None
    if any(ip != ts for ip in ctx.conf.bind_ips):
        print(f"Through an SSH tunnel, open it as http://127.0.0.1:{port}/... instead "
              "(rest of the link unchanged).")
    return 0


def _manual_backup(ctx) -> None:
    st = state.load(ctx.paths)
    service.stop(ctx)
    try:
        b = backup.create(ctx, "manual", st.get("current"))
    finally:
        service.start(ctx)
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


def run_locked(ctx, args) -> int:
    """Run a state-changing command under the op lock; report Busy and crashes."""
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


HUB_CMDS = ("set-token", "bot", "relay", "check", "status", "self-update", "update")
TRANSITIONAL_CMDS = ("bot", "relay", "check", "self-update", "update")


def _hub_main(hub, args) -> int:
    cmd = args.cmd
    if cmd == "set-token":
        from talaria import setup
        return setup.set_token(hub.ctx)
    if cmd == "bot":
        from talaria import telegram
        return telegram.run(hub)
    if cmd in ("relay", "status"):
        from talaria import relay
        if cmd == "relay":
            return relay.main(hub, args.app, args.op)
        print(relay.status_all(hub))
        return 0
    if cmd == "check":
        from talaria import hubcheck
        return hubcheck.check(hub, timer=args.timer)
    from talaria import hubupdate
    if cmd == "self-update":
        return hubupdate.self_update(hub, args.tag)
    return hubupdate.offer(hub, args.tag) if args.offer else hubupdate.start_update(hub, args.tag)


def main(argv: list[str] | None = None, make=make_ctx, make_hub=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["op"]:
        # sudo may keep the hub's environment: talk to this account's own user bus
        os.environ["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
        os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
        from talaria import op
        return op.main(argv[1:])
    args = build_parser().parse_args(argv)
    # `sudo -u hermes talaria …` has no XDG_RUNTIME_DIR, which systemctl --user needs
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if args.cmd == "version":
        print(__version__)
        return 0
    if args.cmd == "setup":
        from talaria import setup
        return setup.setup(args)
    if make_hub is None:     # a caller that injects an app ctx (tests) is an app install
        from talaria import hubexec
        make_hub = hubexec.load_hub if make is make_ctx else (lambda: None)
    hub = make_hub()
    if hub is not None:
        if args.cmd in (TRANSITIONAL_CMDS if hub.transitional else HUB_CMDS):
            return _hub_main(hub, args)
        if not hub.transitional:
            print(f"talaria {args.cmd}: this account is the Talaria hub; app commands run as "
                  "the app's account or through the bot", file=sys.stderr)
            return 2
    if args.cmd in ("relay", "update"):
        print(f"talaria {args.cmd}: this account is not a Talaria hub", file=sys.stderr)
        return 2
    ctx = make()
    if args.cmd in ("deploy", "rehearse", "reject") and not ctx.app.is_release(args.tag):
        print(f"{args.tag} is not a {ctx.app.title} release tag", file=sys.stderr)
        return 2
    if args.cmd == "set-token":
        from talaria import setup
        return setup.set_token(ctx)
    if args.cmd == "bot":
        print("talaria bot: this account has no bot of its own; the hub runs it",
              file=sys.stderr)
        return 1
    if args.cmd == "self-update":     # not under the lock: it runs `setup`, which takes it
        from talaria import selfupdate
        return selfupdate.self_update(ctx, args.tag)
    if args.cmd == "status":
        print(status.status_text(ctx))
        return 0
    if args.cmd == "backups":
        print(status.backups_text(ctx))
        return 0
    if args.cmd == "login-link":
        return login_link(ctx)
    if args.cmd == "reject":
        print(reject(ctx, args.tag))
        return 0
    if args.cmd == "rollback" and not args.confirm:
        print(rollback.describe(ctx))
        return 0
    if args.cmd == "restore" and not args.confirm:
        print(rollback.describe_restore(ctx, args.id))
        return 0
    return run_locked(ctx, args)

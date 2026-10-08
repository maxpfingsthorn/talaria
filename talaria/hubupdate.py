"""Updating Talaria on the whole host: hub first, apps next, bot restart last (spec §6).
Everything this module uses is imported at load time: `self_update` checks out new code
underneath the running process."""
from __future__ import annotations

import sys
import time

from talaria import __version__, lock
from talaria.hubexec import NoAnswer, Unreachable
from talaria.notify import Message
from talaria.shell import CommandError
from talaria.state import ensure_dir


def would_restart(entry, tag: str) -> str:
    try:
        lines = entry.executor.call(["self-update", tag, "--dry-run"], timeout=900)
    except Unreachable:
        return "unknown (sudo rule missing)"
    except NoAnswer:
        return "unknown (no answer)"
    for d in lines:
        if d.get("kind") == "reply" and isinstance(d.get("restart"), bool):
            return "yes" if d["restart"] else "no"
    return "unknown"


def offer(hub, tag: str) -> int:
    parts = [f"{e.title} would restart: {would_restart(e, tag)}" for e in hub.apps.values()]
    hub.ctx.notify.send(Message(
        f"Talaria {tag} is available (installed v{__version__}). " + " · ".join(parts),
        buttons=[[(f"Update Talaria to {tag}", f"hub|up:{tag}")]]))
    return 0


def start_update(hub, tag: str) -> int:
    ctx = hub.ctx
    ctx.sh.run(["systemd-run", "--user", "--collect", "--quiet",
                f"--unit=talaria-update-{int(time.time())}", str(ctx.paths.bin_link),
                "self-update", tag])
    print(f"Updating Talaria to {tag} in the background; the bot reports the result.")
    return 0


def _stop(ctx, text: str) -> int:
    print(text, file=sys.stderr)
    ctx.notify.send(Message(text))
    return 1


def _exit_text(entry, rc) -> str:
    line = getattr(entry.executor, "stderr_line", "")
    return f"exit {rc}: {line}" if line else f"exit {rc}"


def _update_app(entry, tag: str) -> tuple[bool, str]:
    last = ""  # pragma: no mutate  (only used through `last or ...`)
    try:
        for d in entry.executor.stream(["self-update", tag]):
            if d.get("kind") == "reply":
                last = str(d.get("text") or "")
    except Unreachable:
        return False, f"{entry.title} ✗ (sudo rule missing)"
    rc = entry.executor.returncode
    if rc == 0:
        return True, f"{entry.title} ✓"
    return False, f"{entry.title} ✗ ({last or _exit_text(entry, rc)})"


def self_update(hub, tag: str) -> int:
    ctx = hub.ctx
    ensure_dir(ctx.paths.state_dir)
    try:
        with lock.file_lock(ctx.paths.state_dir / "update.lock"):
            return _self_update(hub, tag)
    except lock.Busy:
        return _stop(ctx, f"Talaria {tag}: an update is already running.")


def _self_update(hub, tag: str) -> int:
    ctx = hub.ctx
    if not hub.transitional:      # in transitional mode the hub's install is the app's
        d = str(ctx.paths.install_dir)
        try:
            prev = ctx.sh.run(["git", "-C", d, "rev-parse", "HEAD"]).stdout.strip()
            ctx.sh.run(["git", "-C", d, "fetch", "-q", "--tags", "origin"], timeout=600)
            ctx.sh.run(["git", "-C", d, "checkout", "-q", tag])
        except CommandError as e:
            return _stop(ctx, f"Talaria {tag} was not installed: the hub could not check it "
                              f"out ({e}). Nothing changed.")
        # no restart here: the bot is restarted last, below
        r = ctx.sh.run([str(ctx.paths.bin_link), "setup", "--as-hub", "--no-restart"],
                       check=False, timeout=600)
        print(r.stdout, end="")
        if r.returncode != 0:
            ctx.sh.run(["git", "-C", d, "checkout", "-q", prev], check=False)
            return _stop(ctx, f"Talaria {tag}: the hub's setup failed (exit {r.returncode}); "
                              "the previous version was restored and the apps were not "
                              "updated. Details in the journal.")
    results = [_update_app(e, tag) for e in hub.apps.values()]
    ctx.sh.run(["systemctl", "--user", "restart", "talaria-telegram.service"], check=False)
    ctx.notify.send(Message(f"Talaria {tag} installed: " + " · ".join(t for _, t in results)))
    return 0 if all(ok for ok, _ in results) else 1

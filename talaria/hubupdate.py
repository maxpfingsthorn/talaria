"""Updating Talaria on the whole host: hub first, apps next, bot restart last (spec §6).
Everything this module uses is imported at load time: `self_update` checks out new code
underneath the running process."""
from __future__ import annotations

import subprocess
import sys
import time

from talaria import __version__, lock, relay
from talaria.hubexec import NoAnswer, Unreachable
from talaria.notify import Message
from talaria.selfupdate import fetch_tag, tag_commit
from talaria.shell import CommandError
from talaria.state import ensure_dir
from talaria.tags import SEMVER, semver_newer

FIRST_PIN_RELEASE = "v0.7.3"     # the first app release that accepts `--commit`


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
    retry = tag == f"v{__version__}"
    parts = [f"{e.title} would restart: {would_restart(e, tag)}" for e in hub.apps.values()
             if not retry or relay.lagging(e)]
    head = (f"Retrying Talaria {tag} for apps that lag." if retry
            else f"Talaria {tag} is available (installed v{__version__}).")
    hub.ctx.notify.send(Message(
        f"{head} " + " · ".join(parts),
        buttons=[[(f"Update Talaria to {tag}", f"hub|up:{tag}")]]))
    return 0


def announce(hub, tag: str) -> int:
    """The daily notice: runs nothing on any app. What would restart is looked up only after
    the owner taps (`update <tag> --offer`)."""
    hub.ctx.notify.send(Message(
        f"Talaria {tag} is available (installed v{__version__}).",
        buttons=[[(f"What would {tag} change?", f"hub|ofr:{tag}")]]))
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


def _knows_pin(entry) -> bool:
    """Older apps refuse unknown op arguments, so the commit goes only to a known new one."""
    v = relay.version(entry)
    return bool(v) and bool(SEMVER.match(f"v{v}")) and not semver_newer(FIRST_PIN_RELEASE, f"v{v}")


def _update_app(entry, tag: str, pin: str | None) -> tuple[bool, str]:
    last = ""  # pragma: no mutate  (only used through `last or ...`)
    argv = ["self-update", tag] + (["--commit", pin] if pin and _knows_pin(entry) else [])
    try:
        for d in entry.executor.stream(argv):
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
    retry = tag == f"v{__version__}"   # the hub already runs it: only lagging apps need it
    d = str(ctx.paths.install_dir)
    pin = None
    if not hub.transitional and not retry:      # in transitional mode the hub's install is the app's
        try:
            prev = ctx.sh.run(["git", "-C", d, "rev-parse", "HEAD"]).stdout.strip()
            fetch_tag(ctx.sh, d, tag)
            pin = tag_commit(ctx.sh, d, tag)
            ctx.sh.run(["git", "-C", d, "checkout", "-q", tag])
        except CommandError as e:
            return _stop(ctx, f"Talaria {tag} was not installed: the hub could not check it "
                              f"out ({e}). Nothing changed.")
        # no restart here: the bot is restarted last, below
        setup = [str(ctx.paths.bin_link), "setup", "--as-hub", "--no-restart"]
        try:
            r = ctx.sh.run(setup, check=False, timeout=600)
            ok, out, why = r.returncode == 0, r.stdout, f"failed (exit {r.returncode})"
        except subprocess.TimeoutExpired:
            ok, out, why = False, "", "timed out"
        print(out, end="")
        if not ok:
            ctx.sh.run(["git", "-C", d, "checkout", "-q", prev], check=False)
            try:   # bin_link now runs the old code: it re-renders the old units
                ok2 = ctx.sh.run(setup, check=False, timeout=600).returncode == 0
            except subprocess.TimeoutExpired:
                ok2 = False
            both = "" if ok2 else " (its setup failed too)"
            return _stop(ctx, f"Talaria {tag}: the hub's setup {why}; the previous version "
                              f"was restored{both} and the apps were not updated. Details "
                              "in the journal.")
    else:
        try:
            pin = tag_commit(ctx.sh, d, tag)
        except CommandError:
            pass
    todo = [e for e in hub.apps.values() if not retry or relay.lagging(e)]
    if retry and not todo:
        ctx.notify.send(Message(f"Talaria {tag}: no app lags behind; nothing to do."))
        return 0
    results = [_update_app(e, tag, pin) for e in todo]
    ctx.sh.run(["systemctl", "--user", "restart", "talaria-telegram.service"], check=False)
    at = f" (commit {pin[:12]})" if pin else ""
    ctx.notify.send(Message(f"Talaria {tag} installed{at}: " + " · ".join(t for _, t in results)))
    return 0 if all(ok for ok, _ in results) else 1

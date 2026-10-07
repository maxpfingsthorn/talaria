"""The hub's daily check (spec §5.4) and the one Talaria-release check per host (§6)."""
from __future__ import annotations

import getpass
import json
import sys
import traceback

from talaria import __version__, hubupdate, relay
from talaria.notify import Message
from talaria.shell import CommandError
from talaria.state import write_json_atomic
from talaria.tags import semver_newer
from talaria.upstream import latest_semver

TRANSITIONAL = ("Talaria v0.5 needs a one-time move of the bot to its own user: run "
                "`bin/talaria setup --app {app} --user {user}` as the operator.")


def load_state(paths) -> dict:
    try:
        return json.loads(paths.hub_state.read_text())
    except (OSError, ValueError):
        return {}


def check(hub, timer: bool) -> int:
    for name, e in hub.apps.items():
        try:
            err = relay.hello(e)
            if err:
                hub.ctx.notify.send(Message(f"{e.title}: {err}" if err == relay.VERSIONS else err))
                continue
            relay.relay(hub, name, ["check", "--timer"] if timer else ["check"])
        except Exception as x:    # one app must not stop the others or the release check
            traceback.print_exc(file=sys.stderr)
            hub.ctx.notify.send(Message(f"{e.title}: the check failed unexpectedly ({x})"))
    if hub.transitional and timer:
        (app,) = hub.apps
        hub.ctx.notify.send(Message(TRANSITIONAL.format(app=app, user=getpass.getuser())))
    talaria_release(hub)
    return 0


def talaria_release(hub) -> None:
    ctx = hub.ctx
    try:
        latest = latest_semver(ctx.sh, ctx.conf.talaria_repo)
    except CommandError:
        return
    if not latest or not semver_newer(latest, f"v{__version__}"):
        return
    st = load_state(ctx.paths)
    if st.get("talaria_notified") == latest:
        return
    hubupdate.offer(hub, latest)
    st["talaria_notified"] = latest
    write_json_atomic(ctx.paths.hub_state, st)

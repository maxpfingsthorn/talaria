"""The hub's daily check (spec §5.4) and the one Talaria-release check per host (§6)."""
from __future__ import annotations

import getpass
import json
import re
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


def check(hub, timer: bool, report: bool = False) -> int:
    """`report`: the person asked (/check), so the run ends with a summary, nothing new or not."""
    failed, news, current = [], [], []
    for name, e in hub.apps.items():
        try:
            err = relay.hello(e)
            if err:
                hub.ctx.notify.send(Message(f"{e.title}: {err}" if err.startswith(relay.VERSIONS) else err))
                failed.append(e.title)
                continue
            rc, sent = relay.relay_sent(hub, name, ["check", "--timer"] if timer else ["check"])
        except Exception as x:    # one app must not stop the others or the release check
            traceback.print_exc(file=sys.stderr)  # pragma: no mutate  (stderr is the default)
            hub.ctx.notify.send(Message(f"{e.title}: the check failed unexpectedly ({x})"))
            failed.append(e.title)
            continue
        if rc != 0:
            failed.append(e.title)
        elif sent:
            news.append(e.title)
        elif report:
            current.append(_current(e))
    if hub.transitional and timer:
        (app,) = hub.apps
        hub.ctx.notify.send(Message(TRANSITIONAL.format(app=app, user=getpass.getuser())))
    state = talaria_release(hub, force=report)
    if report:
        if state == "failed":
            failed.append("Talaria")
        elif state == "new":
            news.append("Talaria")
        else:
            current.append(f"Talaria v{__version__}")
        hub.ctx.notify.send(Message(summary(news, failed, current)))
    return 0


def _current(entry) -> str:
    """The app's title and, when its status says so, its current tag."""
    text, _ = relay.quick(entry, ["status"])
    m = re.match(rf"{re.escape(entry.title)} (v?\d\S*) \(", text)
    return f"{entry.title} {m.group(1)}" if m else entry.title


def summary(news: list, failed: list, current: list) -> str:
    parts = ["Check finished." if news or failed else "No new releases."]
    if news:
        parts.append(f"News from {', '.join(news)} above.")
    if failed:
        parts.append(f"Could not check {', '.join(failed)}.")
    if current:
        parts.append(f"{', '.join(current)} {'is' if len(current) == 1 else 'are'} current.")
    return " ".join(parts)


def check_talaria(hub) -> int:
    """/check talaria: only the Talaria release; the answer is always a message."""
    state = talaria_release(hub, force=True)
    if state == "failed":
        hub.ctx.notify.send(Message("Talaria: could not look up the latest release."))
    elif state == "current":
        hub.ctx.notify.send(Message(f"Talaria v{__version__} is current."))
    return 0


def talaria_release(hub, force: bool = False) -> str:
    """"new" (offered), "current", or "failed" (the lookup did not work). `force`: offer
    again a release that was offered before."""
    ctx = hub.ctx
    try:
        latest = latest_semver(ctx.sh, ctx.conf.talaria_repo)
    except CommandError:
        return "failed"
    if not latest or not semver_newer(latest, f"v{__version__}"):
        return "current"
    st = load_state(ctx.paths)
    if st.get("talaria_notified") == latest and not force:
        return "current"
    hubupdate.offer(hub, latest)
    st["talaria_notified"] = latest
    write_json_atomic(ctx.paths.hub_state, st)
    return "new"

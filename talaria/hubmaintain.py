"""The hub's maintenance tick (spec 2026-10-09 §5.2): `talaria maintain [--timer]`, run by
talaria-maintain.timer every 15 minutes. Each app with a maintenance hook decides itself
whether its window is due; the hub forwards what it says. Not reaching an app goes to the
journal only: the daily check reports that."""
from __future__ import annotations

import sys
import traceback

from talaria import apps, relay


def maintain(hub, timer: bool) -> int:
    argv = ["maintain", "--timer"] if timer else ["maintain"]
    for name, e in hub.apps.items():
        if not apps.get(name).has_maintenance:
            continue
        try:
            for d in e.executor.stream(argv):
                if d.get("kind") == "message":
                    hub.ctx.notify.send(relay.to_message(name, d))
        except Exception as x:    # one app must not stop the others
            traceback.print_exc(file=sys.stderr)  # pragma: no mutate  (stderr is the default)
            print(f"[talaria] {name}: maintain failed: {x!r}", file=sys.stderr)
            continue
        rc = e.executor.returncode
        if rc:
            print(f"[talaria] {name}: maintain exited {rc}: {e.executor.stderr_line}",
                  file=sys.stderr)
    return 0

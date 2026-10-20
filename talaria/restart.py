"""`talaria restart`: stop and start the app's existing container; nothing else changes."""
from __future__ import annotations

from talaria import service, state
from talaria.notify import Message
from talaria.rollback import interrupted


def run(ctx, st: dict) -> None:
    title = ctx.app.title
    why = interrupted(ctx, st)
    if why:
        ctx.notify.send(Message(f"{title} not restarted: {why}."))
        return
    # a crash from here on is recovered by /rollback CONFIRM, which starts the app again
    st["op"] = {"op": "restart", "changed": False, "started": ctx.now().isoformat()}
    state.save(ctx.paths, st)
    try:
        service.stop(ctx)
        service.start(ctx)
        down = service.post_start_check(ctx)
    except Exception as e:                  # report it; the op record stays for /rollback
        down = f"{type(e).__name__}: {e}"
    if down:
        ctx.notify.send(Message(f"{title} did not come back after the restart. Send /rollback "
                                f"CONFIRM to start it again; nothing is restored.",
                                untrusted=[("Last error", down)]))
        return
    st["op"] = None
    state.save(ctx.paths, st)
    ctx.notify.send(Message(f"{title} restarted and running."))

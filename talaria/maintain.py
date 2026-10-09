"""The nightly maintenance window, app side (spec 2026-10-09 §5.2): `talaria maintain
[--timer]`. The hub's talaria-maintain.timer asks every 15 minutes; this module decides
whether the window is due, then stops the app, runs its hook and starts it again."""
from __future__ import annotations

from datetime import datetime, timedelta

from talaria import service, state
from talaria.ctx import local_now
from talaria.notify import Message
from talaria.rollback import interrupted

WINDOW = timedelta(hours=2)


def window_start(ctx) -> datetime | None:
    """Start of the maintenance window the local time is in now; None without a window or
    outside it. A window that started before midnight still counts after it."""
    t = ctx.conf.maintenance_time
    if not t:
        return None
    now = local_now(ctx)
    h, m = (int(x) for x in t.split(":"))
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if start > now:
        start -= timedelta(days=1)
    return start if now < start + WINDOW else None


def timer_day(ctx, st: dict) -> str | None:
    """The date of the window the timer should run now, or None when nothing is due:
    no hook, outside the window, tonight already done, or the app is not running
    (stopped on purpose or broken: /status says so). Read-only."""
    if not ctx.app.has_maintenance:
        return None
    start = window_start(ctx)
    if start is None:
        return None
    day = start.date().isoformat()
    if (st.get("maintenance") or {}).get("date") == day:
        return None
    return day if service.is_active(ctx) else None


def run(ctx, st: dict, timer: bool) -> None:
    app = ctx.app
    if not app.has_maintenance:
        if not timer:
            ctx.notify.send(Message(f"{app.title} has no maintenance."))
        return
    if timer:
        day = timer_day(ctx, st)
        if day is None:
            return
        was_active = True
    else:                                   # a manual run neither uses up a night nor
        day = None  # pragma: no mutate  (falsy either way); a manual run starts no stopped app
        was_active = service.is_active(ctx)
    why = interrupted(ctx, st)
    if why:
        if not timer:
            ctx.notify.send(Message(f"{app.title} maintenance not run: {why}."))
        return
    if day:
        st["maintenance"] = {"date": day, "ok": False}
        state.save(ctx.paths, st)           # a crash below must not repeat the run tonight
    try:
        if was_active:
            service.stop(ctx)
        reason = app.maintenance(ctx)
    except Exception as e:                  # the app must come back whatever the hook did
        reason = f"{type(e).__name__}: {e}"
    down = None  # pragma: no mutate  (falsy either way)
    if was_active:
        try:
            service.start(ctx)
            down = service.post_start_check(ctx)
        except Exception as e:
            down = str(e)
    if down:
        reason = f"{reason}; then {down}" if reason else down
    if day:
        st["maintenance"]["ok"] = reason is None
    if reason:
        ctx.notify.send(Message(f"{app.title} maintenance failed. Talaria tries again next "
                                "night.", untrusted=[("Last error", reason)]))
    elif not timer:
        ctx.notify.send(Message(f"{app.title} maintenance finished."))

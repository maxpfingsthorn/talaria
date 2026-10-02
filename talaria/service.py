from __future__ import annotations


def _sc(ctx, *args, check=True):
    return ctx.sh.run(["systemctl", "--user", *args], check=check, timeout=600)


def stop(ctx) -> None:
    _sc(ctx, "stop", ctx.app.unit)


def start(ctx) -> None:
    _sc(ctx, "reset-failed", ctx.app.unit, check=False)
    _sc(ctx, "start", ctx.app.unit)


def is_active(ctx) -> bool:
    return _sc(ctx, "is-active", ctx.app.unit, check=False).stdout.strip() == "active"


def nrestarts(ctx) -> int:
    return int(_sc(ctx, "show", "-p", "NRestarts", "--value", ctx.app.unit).stdout.strip() or 0)


def post_start_check(ctx) -> str | None:
    base = nrestarts(ctx)
    waited = 0
    while waited < ctx.conf.settle_seconds:
        ctx.sleep(5)
        waited += 5
        if not is_active(ctx):
            return f"{ctx.app.unit} is not active"
        if nrestarts(ctx) != base:
            return f"{ctx.app.unit} restarted"
    reason = None  # pragma: no mutate  (overwritten before it is returned)
    for _ in range(12):
        reason = ctx.app.health(ctx)
        if reason is None:
            return None
        ctx.sleep(5)
    return reason

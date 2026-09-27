from __future__ import annotations

from datetime import datetime

from talaria import backup, disk, hermes, images, marker, retention, state
from talaria.notify import Message
from talaria.restore import restore_data

MANUAL = "Manual recovery: see README, section 'Manual recovery'."


class Unavailable(Exception):
    pass


def age(ctx, iso: str) -> str:
    s = int((ctx.now() - datetime.fromisoformat(iso)).total_seconds())
    return f"{s // 86400}d" if s >= 86400 else f"{s // 3600}h" if s >= 3600 else f"{s // 60}m"


def target(ctx, st) -> tuple[str, dict] | None:
    m = marker.read(ctx.paths)
    if m:
        return m["backup"], m["image"]
    if st.get("last_deploy") and st.get("previous"):
        return st["last_deploy"]["backup"], st["previous"]
    return None


def describe(ctx) -> str:
    st = state.load(ctx.paths)
    t = target(ctx, st)
    if not t:
        return "Nothing to roll back: no interrupted change and no previous deploy."
    b = backup.get(ctx, t[0])
    return (f"Rollback restores backup {b.id} ({age(ctx, b.meta['created'])} old) and "
            f"Hermes {t[1].get('tag')}. Everything Hermes wrote since then is lost.\n"
            "Send /rollback CONFIRM to proceed.")


def _swap(ctx, b, image: dict, op: str, revert: tuple[str, dict]) -> str | None:
    """Stop, restore b, switch to image, start, check. The marker points at `revert`."""
    hermes.stop(ctx)
    marker.write(ctx.paths, op, revert[0], revert[1], ctx.now())
    restore_data(ctx, b)
    st = state.load(ctx.paths)
    st["current"], st["previous"], st["last_deploy"] = image, None, None
    state.save(ctx.paths, st)
    images.retag(ctx, "current", image)
    marker.clear(ctx.paths)
    hermes.start(ctx)
    return hermes.post_start_check(ctx)


def rollback(ctx) -> str | None:
    st = state.load(ctx.paths)
    t = target(ctx, st)
    if not t:
        raise Unavailable()
    bid, image = t
    b = backup.get(ctx, bid)
    backup.verify(b)
    images.ensure(ctx, image)
    disk.ensure_space(ctx, int(b.meta.get("data_size", 0)), ctx.conf.data_dir.parent)
    op = (marker.read(ctx.paths) or {}).get("op", "rollback")
    if not st.get("op"):
        st["op"] = {"op": "rollback", "backup": bid, "started": ctx.now().isoformat()}
        state.save(ctx.paths, st)
    reason = _swap(ctx, b, image, op, (bid, image))
    if reason:
        hermes.stop(ctx)
        marker.write(ctx.paths, op, bid, image, ctx.now())
        return reason
    st = state.load(ctx.paths)
    st["op"] = None
    state.save(ctx.paths, st)
    return None


def rollback_cmd(ctx) -> None:
    try:
        reason = rollback(ctx)
    except Unavailable:
        ctx.notify.send(Message("Nothing to roll back: no interrupted change and no "
                                "previous deploy."))
        return
    except Exception as e:
        ctx.notify.send(Message(f"Rollback failed: {e}. Hermes may be stopped. {MANUAL}"))
        return
    if reason:
        ctx.notify.send(Message(f"Rollback failed: {reason}. Hermes is stopped. {MANUAL}"))
        return
    tag = state.load(ctx.paths)["current"].get("tag")
    ctx.notify.send(Message(f"Rolled back to Hermes {tag}."))


def describe_restore(ctx, bid: str) -> str:
    try:
        b = backup.get(ctx, bid)
    except KeyError:
        return f"No backup {bid}. /backups lists them."
    return (f"Restore replaces all Hermes data with backup {b.id} "
            f"({age(ctx, b.meta['created'])} old, Hermes {(b.meta.get('image') or {}).get('tag')}). "
            "Talaria first takes a pre-restore backup, so this can be undone.\n"
            f"Send /restore {b.id} CONFIRM to proceed.")


def restore_cmd(ctx, bid: str) -> None:
    try:
        b = backup.get(ctx, bid)
    except KeyError:
        ctx.notify.send(Message(f"No backup {bid}. /backups lists them."))
        return
    try:
        backup.verify(b)
        image = b.meta.get("image")
        if not image:
            raise images.ImageMissing("the backup records no image")
        images.ensure(ctx, image)
        disk.ensure_space(ctx, 2 * int(b.meta.get("data_size", 0)), ctx.conf.data_dir.parent)
    except Exception as e:
        ctx.notify.send(Message(f"Restore of {bid} refused: {e}. Nothing was changed."))
        return
    st = state.load(ctx.paths)
    old = st["current"]
    hermes.stop(ctx)
    pre = backup.create(ctx, "pre-restore", old)
    st["op"] = {"op": "restore", "backup": bid, "started": ctx.now().isoformat()}
    state.save(ctx.paths, st)
    reason = _swap(ctx, b, image, "restore", (pre.id, old))
    if reason:
        marker.write(ctx.paths, "restore", pre.id, old, ctx.now())
        again = rollback(ctx)
        tail = "Hermes reverted to the state before the restore." if again is None else \
            f"Reverting failed too: {again}. Hermes is stopped. {MANUAL}"
        ctx.notify.send(Message(f"Restore of {bid} failed: {reason}. {tail}"))
        return
    st = state.load(ctx.paths)
    st["op"] = None
    retention.apply(ctx, st)
    state.save(ctx.paths, st)
    ctx.notify.send(Message(f"Restored backup {bid} (Hermes {image.get('tag')}).",
                            commands=[f"/restore {pre.id} CONFIRM"]))

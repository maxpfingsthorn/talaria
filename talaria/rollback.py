from __future__ import annotations

from datetime import datetime

from talaria import backup, disk, hermes, images, marker, retention, state
from talaria.notify import Message
from talaria.restore import restore_data

MANUAL = "Manual recovery: see README, section 'Manual recovery'."


class Unavailable(Exception):
    pass


class Refused(Exception):
    """Nothing was changed; Hermes keeps running."""


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


def interrupted(ctx, st) -> str | None:
    """Why nothing new may start: an interrupted change must be recovered first."""
    rec = st.get("op") or marker.read(ctx.paths)
    if rec:
        return f"an interrupted {rec['op']} must be recovered first: send /rollback CONFIRM"
    return None


def needs_resume(ctx, st) -> bool:
    """An interrupted op without a marker left data and image consistent: it changed
    nothing yet, or it was a rollback/restore whose swap had finished."""
    op = st.get("op")
    return bool(op) and marker.read(ctx.paths) is None and (
        not op.get("changed") or op["op"] in ("rollback", "restore"))


def resume(ctx) -> str | None:
    hermes.start(ctx)
    reason = hermes.post_start_check(ctx)
    if reason:
        hermes.stop(ctx)
        return reason
    st = state.load(ctx.paths)
    st["op"] = None
    state.save(ctx.paths, st)
    return None


def describe(ctx) -> str:
    st = state.load(ctx.paths)
    if needs_resume(ctx, st):
        return (f"The interrupted {st['op']['op']} left nothing to undo. Send /rollback CONFIRM "
                "to start Hermes and check it.")
    t = target(ctx, st)
    if not t:
        return "Nothing to roll back: no interrupted change and no previous deploy."
    b = backup.get(ctx, t[0])
    undo = ("" if marker.read(ctx.paths) else
            " Talaria first takes a pre-rollback backup, so this can be undone.")
    return (f"Rollback restores backup {b.id} ({age(ctx, b.meta['created'])} old) and "
            f"Hermes {t[1].get('tag')}. Everything Hermes wrote since then is replaced.{undo}\n"
            "Send /rollback CONFIRM to proceed.")


def describe_buttons(ctx) -> list:
    st = state.load(ctx.paths)
    if needs_resume(ctx, st):
        return [[("Start Hermes and check it", "rb:resume")]]
    t = target(ctx, st)
    if not t:
        return []
    return [[(f"Roll back to Hermes {t[1].get('tag')}", f"rb:{t[0]}:{stamp(ctx)}")]]


def describe_restore_buttons(ctx, bid: str) -> list:
    try:
        b = backup.get(ctx, bid)
    except KeyError:
        return []
    return [[(f"Restore {b.id}", f"rs:{b.id}:{stamp(ctx)}")]]


BUTTON_MINUTES = 60


def stamp(ctx) -> int:
    """Minutes since the epoch, carried in rollback and restore buttons so they expire."""
    return int(ctx.now().timestamp()) // 60


def fresh(ctx, minute: str) -> bool:
    return minute.isdigit() and 0 <= stamp(ctx) - int(minute) <= BUTTON_MINUTES


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


def rollback(ctx) -> tuple[str | None, str | None]:
    """(failure reason, pre-rollback backup id). A rollback the user asks for (no
    interrupted change) first backs up the current data, so it can be undone."""
    st = state.load(ctx.paths)
    t = target(ctx, st)
    if not t:
        raise Unavailable()
    bid, image = t
    b = backup.get(ctx, bid)
    backup.verify(b)
    images.ensure(ctx, image)
    m = marker.read(ctx.paths)
    live = m is None            # no marker: Hermes may have written to this data; keep it
    disk.ensure_space(ctx, (2 if live else 1) * int(b.meta.get("data_size", 0)),
                      ctx.conf.data_dir.parent)
    op = (m or {}).get("op", "rollback")
    pre = None
    ours = not st.get("op")
    if ours:
        st["op"] = {"op": "rollback", "backup": bid, "changed": not live,
                    "started": ctx.now().isoformat()}
        state.save(ctx.paths, st)
    if live:
        try:
            hermes.stop(ctx)
            pre = backup.create(ctx, "pre-rollback", st["current"]).id
        except Exception as e:
            hermes.start(ctx)
            if ours:
                st["op"] = None
                state.save(ctx.paths, st)
            raise Refused(f"the pre-rollback backup failed: {e}") from e
        st["op"]["changed"] = True
        state.save(ctx.paths, st)
    reason = _swap(ctx, b, image, op, (bid, image))
    if reason:
        hermes.stop(ctx)
        marker.write(ctx.paths, op, bid, image, ctx.now())
        return reason, pre
    st = state.load(ctx.paths)
    st["op"] = None
    retention.apply(ctx, st)
    state.save(ctx.paths, st)
    return None, pre


def rollback_cmd(ctx) -> None:
    st = state.load(ctx.paths)
    if needs_resume(ctx, st):
        op = st["op"]["op"]
        try:
            reason = resume(ctx)
        except Exception as e:
            reason = str(e)
        if reason:
            ctx.notify.send(Message(f"Recovery failed: {reason}. Hermes is stopped. {MANUAL}"))
        else:
            ctx.notify.send(Message(f"Hermes {(st.get('current') or {}).get('tag')} is running "
                                    f"again. The interrupted {op} had nothing left to undo."))
        return
    try:
        reason, pre = rollback(ctx)
    except Unavailable:
        ctx.notify.send(Message("Nothing to roll back: no interrupted change and no "
                                "previous deploy."))
        return
    except Refused as e:
        ctx.notify.send(Message(f"Rollback refused: {e}. Nothing was changed; Hermes is "
                                "running."))
        return
    except Exception as e:
        ctx.notify.send(Message(f"Rollback failed: {e}. Hermes may be stopped. {MANUAL}"))
        return
    if reason:
        keep = f" The data from before the rollback is in backup {pre}." if pre else ""
        ctx.notify.send(Message(f"Rollback failed: {reason}. Hermes is stopped.{keep} {MANUAL}",
                                commands=[f"/restore {pre} CONFIRM"] if pre else []))
        return
    tag = state.load(ctx.paths)["current"].get("tag")
    ctx.notify.send(Message(f"Rolled back to Hermes {tag}.",
                            commands=[f"/restore {pre} CONFIRM"] if pre else []))


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
    st["op"] = {"op": "restore", "backup": bid, "changed": False,
                "started": ctx.now().isoformat()}
    state.save(ctx.paths, st)
    try:
        hermes.stop(ctx)
        pre = backup.create(ctx, "pre-restore", old)
    except Exception as e:
        hermes.start(ctx)
        st["op"] = None
        state.save(ctx.paths, st)
        ctx.notify.send(Message(f"Restore of {bid} failed before changing anything: {e}. "
                                "Hermes was started again."))
        return
    st["op"]["changed"] = True
    state.save(ctx.paths, st)
    reason = _swap(ctx, b, image, "restore", (pre.id, old))
    if reason:
        marker.write(ctx.paths, "restore", pre.id, old, ctx.now())
        again, _ = rollback(ctx)
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

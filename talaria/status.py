from __future__ import annotations

from talaria import __version__, backup, disk, hermes, lock, marker, state
from talaria.rollback import age
from talaria.tags import semver_newer


def interrupted_text(ctx, st) -> str | None:
    op = st.get("op")
    if not op:
        return None
    what = f"{op['op']} {op.get('tag') or op.get('backup') or ''}".strip()
    try:
        with lock.op_lock(ctx.paths):
            pass
    except lock.Busy:
        return f"An operation is in progress: {what}."
    when = age(ctx, op["started"])
    if marker.read(ctx.paths):
        return (f"Interrupted {what} ({when} ago). Hermes is stopped. "
                "Send /rollback CONFIRM to restore the state before it.")
    return (f"Interrupted {what} ({when} ago): the new version is running but was not "
            "verified. /rollback CONFIRM goes back.")


def status_text(ctx) -> str:
    st = state.load(ctx.paths)
    cur = st.get("current") or {}
    running = "running" if hermes.is_active(ctx) else "not running"
    lines = [f"Hermes {cur.get('tag', 'unknown')} ({(cur.get('id') or '')[7:19]}), {running}.",
             f"{disk.free_bytes(ctx.conf.data_dir) / disk.GB:.1f} GB free."]
    if st.get("pending"):
        t = st["pending"]["tag"]
        lines.append(f"Pending: {t}. /approve {t} · /reject {t}")
    it = interrupted_text(ctx, st)
    if it:
        lines.append(it)
    n = st.get("talaria_notified")
    if n and semver_newer(n, f"v{__version__}"):
        lines.append(f"Talaria {n} is available (installed v{__version__}).")
    return "\n".join(lines)


def backups_text(ctx) -> str:
    bs = backup.list_backups(ctx)
    if not bs:
        return "No backups yet."
    return "\n".join(
        f"{b.id}  {b.meta.get('label')}  {age(ctx, b.meta['created'])} old  "
        f"{b.meta.get('size', 0) / disk.GB:.2f} GB  Hermes {(b.meta.get('image') or {}).get('tag')}"
        for b in bs)

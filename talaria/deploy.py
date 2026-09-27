from __future__ import annotations

import os
import signal

from talaria import backup, disk, hermes, history, images, marker, retention, state
from talaria.containers import run_helper
from talaria.notify import Message
from talaria.rollback import MANUAL, rollback
from talaria.state import ensure_dir


def _crash_point(name: str) -> None:
    """e2e hook: TALARIA_TEST_CRASH_AT=<name> kills the process here."""
    if os.environ.get("TALARIA_TEST_CRASH_AT") == name:
        os.kill(os.getpid(), signal.SIGKILL)


def _fail(ctx, tag: str, reason: str, bid: str, revert_image: dict, details=()) -> None:
    marker.write(ctx.paths, "deploy", bid, revert_image, ctx.now())
    try:
        again = rollback(ctx)
    except Exception as e:
        again = str(e)
    st = state.load(ctx.paths)
    if tag not in st["failed"]:
        st["failed"].append(tag)
    if (st.get("pending") or {}).get("tag") == tag:
        st["pending"] = None
    state.save(ctx.paths, st)
    tail = f"Rolled back to Hermes {revert_image.get('tag')}." if again is None else \
        f"Rollback also failed: {again}. Hermes is stopped. {MANUAL}"
    ctx.notify.send(Message(f"Hermes {tag} failed during deploy: {reason}. {tail}",
                            untrusted=list(details)))


def deploy(ctx, tag: str) -> None:
    st = state.load(ctx.paths)
    p = st.get("pending")
    if not p or p["tag"] != tag:
        ctx.notify.send(Message(f"Nothing deployed: {tag} is not the pending candidate."))
        return
    data = ctx.conf.data_dir
    try:
        disk.ensure_space(ctx, 2 * disk.dir_size(data), data.parent)
        images.ensure(ctx, p["image"])
    except Exception as e:
        ctx.notify.send(Message(f"Deploy of {tag} refused: {e}. Nothing was stopped."))
        return
    old = st["current"]
    # recorded before Hermes stops, so a crash in the backup can be recovered (§7.7)
    st["op"] = {"op": "deploy", "tag": tag, "backup": None, "changed": False,
                "started": ctx.now().isoformat()}
    state.save(ctx.paths, st)
    try:
        hermes.stop(ctx)
        history.commit(ctx, st, f"before deploy {tag}")
        b = backup.create(ctx, f"pre-{tag}", old)
    except Exception as e:
        hermes.start(ctx)
        st["op"] = None
        state.save(ctx.paths, st)
        ctx.notify.send(Message(f"Deploy of {tag} failed before changing anything: {e}. "
                                "Hermes was started again."))
        return
    st["op"].update(backup=b.id, changed=True)
    state.save(ctx.paths, st)
    marker.write(ctx.paths, "deploy", b.id, old, ctx.now())
    _crash_point("after_marker")
    try:
        ensure_dir(ctx.paths.staging)
        mig = run_helper(ctx, p["image"], "migrate.py", data, ctx.paths.staging)
    except Exception as e:
        return _fail(ctx, tag, f"migration could not run: {e}", b.id, old)
    if not mig["ok"]:
        return _fail(ctx, tag, f"migration failed: {mig['error']}", b.id, old, mig["messages"])
    if mig["after"] != p["cfg_after"]:
        return _fail(ctx, tag, f"config version {mig['after']}, expected {p['cfg_after']} "
                               "from the rehearsal", b.id, old, mig["messages"])
    st["previous"], st["current"] = old, p["image"]
    st["last_deploy"], st["pending"] = {"tag": tag, "backup": b.id}, None
    state.save(ctx.paths, st)
    images.retag(ctx, "previous", old)
    images.retag(ctx, "current", p["image"])
    marker.clear(ctx.paths)
    try:
        hermes.start(ctx)
        reason = hermes.post_start_check(ctx)
    except Exception as e:
        reason = str(e)
    if reason:
        return _fail(ctx, tag, reason, b.id, old)
    st = state.load(ctx.paths)
    st["op"], st["adopt_backup"] = None, None
    retention.apply(ctx, st)
    state.save(ctx.paths, st)
    ctx.notify.send(Message(f"Deployed Hermes {tag}.", commands=["/rollback"]))

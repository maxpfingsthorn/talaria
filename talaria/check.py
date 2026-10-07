from __future__ import annotations

from talaria import history
from talaria.notify import Message
from talaria.rehearse import Permanent, Transient, rehearse
from talaria.rollback import interrupted
from talaria.shell import CommandError
from talaria.tags import pick_candidate, releases


def _run_rehearsal(ctx, st, tag: str, commit: str) -> None:
    try:
        rehearse(ctx, st, tag, commit)
        st["transient"] = None
    except Transient as e:
        reason = str(e)
        if (st.get("transient") or {}).get("reason") != reason:
            ctx.notify.send(Message(f"Could not rehearse {ctx.app.title} {tag}: {reason}. "
                                    "Talaria retries at the next check."))
        st["transient"] = {"tag": tag, "reason": reason}
    except Permanent as e:
        if tag not in st["failed"]:
            st["failed"].append(tag)
        ctx.notify.send(Message(f"{ctx.app.title} {tag} failed the rehearsal: {e}. "
                                "Production was not touched.", untrusted=e.details))


def check(ctx, st: dict) -> None:
    history.commit(ctx, st, "daily")
    if interrupted(ctx, st):     # /status and the bot's startup notice report it
        return
    try:
        git = releases(ctx)
        reg = ctx.app.published(ctx, list(git))
    except CommandError as e:
        st["check_failures"] = st.get("check_failures", 0) + 1
        if st["check_failures"] == 3:
            ctx.notify.send(Message(f"Talaria could not check for releases for 3 days: {e}"))
        return
    st["check_failures"] = 0
    current = (st.get("current") or {}).get("tag")
    cand = pick_candidate(ctx.app, git, reg, current, set(st["rejected"]) | set(st["failed"]),
                          ctx.conf.min_release)
    if not cand or (st.get("pending") or {}).get("tag") == cand:
        return
    _run_rehearsal(ctx, st, cand, git[cand])


def rehearse_tag(ctx, st: dict, tag: str) -> None:
    why = interrupted(ctx, st)
    if why:
        ctx.notify.send(Message(f"Not rehearsing {tag}: {why}."))
        return
    git = ctx.app.releases(ctx)
    if tag not in git:
        ctx.notify.send(Message(f"{tag} is not a release tag of {ctx.conf.repo}."))
        return
    if tag in st["failed"]:
        st["failed"].remove(tag)
    _run_rehearsal(ctx, st, tag, git[tag])

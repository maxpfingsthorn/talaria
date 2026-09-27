from __future__ import annotations

from talaria import __version__, history
from talaria.notify import Message
from talaria.rehearse import Permanent, Transient, rehearse
from talaria.shell import CommandError
from talaria.tags import pick_candidate, semver_newer
from talaria.upstream import git_release_tags, latest_semver, registry_tags


def _talaria_reminder(ctx, st) -> None:
    try:
        latest = latest_semver(ctx.sh, ctx.conf.talaria_repo)
    except CommandError:
        return
    if latest and semver_newer(latest, f"v{__version__}") and st["talaria_notified"] != latest:
        ctx.notify.send(Message(
            f"Talaria {latest} is available (installed v{__version__}). "
            f"Update when convenient: talaria self-update {latest}"))
        st["talaria_notified"] = latest


def _run_rehearsal(ctx, st, tag: str, commit: str) -> None:
    try:
        rehearse(ctx, st, tag, commit)
        st["transient"] = None
    except Transient as e:
        reason = str(e)
        if (st.get("transient") or {}).get("reason") != reason:
            ctx.notify.send(Message(f"Could not rehearse Hermes {tag}: {reason}. "
                                    "Talaria retries at the next check."))
        st["transient"] = {"tag": tag, "reason": reason}
    except Permanent as e:
        if tag not in st["failed"]:
            st["failed"].append(tag)
        ctx.notify.send(Message(f"Hermes {tag} failed the rehearsal: {e}. "
                                "Production was not touched.", untrusted=e.details))


def check(ctx, st: dict) -> None:
    history.commit(ctx, st, "daily")
    _talaria_reminder(ctx, st)
    try:
        git = git_release_tags(ctx.sh, ctx.conf.hermes_repo)
        reg = registry_tags(ctx.sh, ctx.conf.image, ctx.conf.registry_tls_verify)
    except CommandError as e:
        st["check_failures"] = st.get("check_failures", 0) + 1
        if st["check_failures"] == 3:
            ctx.notify.send(Message(f"Talaria could not check for releases for 3 days: {e}"))
        return
    st["check_failures"] = 0
    current = (st.get("current") or {}).get("tag")
    cand = pick_candidate(git, reg, current, set(st["rejected"]) | set(st["failed"]),
                          ctx.conf.min_release)
    if not cand or (st.get("pending") or {}).get("tag") == cand:
        return
    _run_rehearsal(ctx, st, cand, git[cand])


def rehearse_tag(ctx, st: dict, tag: str) -> None:
    git = git_release_tags(ctx.sh, ctx.conf.hermes_repo)
    if tag not in git:
        ctx.notify.send(Message(f"{tag} is not a release tag of {ctx.conf.hermes_repo}."))
        return
    if tag in st["failed"]:
        st["failed"].remove(tag)
    _run_rehearsal(ctx, st, tag, git[tag])

from __future__ import annotations

import re
from typing import Iterable

RELEASE_TAG = re.compile(r"^v(20\d\d)\.(\d{1,2})\.(\d{1,2})(?:\.(\d+))?$")
SEMVER = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
TAG_ARG = re.compile(r"^v\d{1,4}(\.\d{1,4}){2,3}$")


def is_release(t: str) -> bool:
    return bool(RELEASE_TAG.match(t))


def key(t: str) -> tuple[int, int, int, int]:
    m = RELEASE_TAG.match(t)
    if not m:
        raise ValueError(f"not a release tag: {t!r}")
    y, mo, d, s = m.groups()
    return int(y), int(mo), int(d), int(s or 0)


def releases(ctx) -> dict[str, str]:
    """ctx.app.releases(ctx), narrowed to ctx.conf.release_allow when that test-only key
    is set (spec §10). Used by check.check and setup._fresh_image; rehearse_tag (an
    explicit tag) stays unfiltered."""
    git = ctx.app.releases(ctx)
    if ctx.conf.release_allow:
        git = {t: c for t, c in git.items() if t in ctx.conf.release_allow}
    return git


def pick_candidate(app, git_tags: Iterable[str], published: set[str], current: str | None,
                   excluded: set[str], floor: str) -> str | None:
    ok = [t for t in git_tags
          if app.is_release(t) and t in published and t not in excluded
          and app.tag_key(t) >= app.tag_key(floor)
          and (current is None or app.tag_key(t) > app.tag_key(current))]
    return max(ok, key=app.tag_key) if ok else None


def semver_newer(a: str, b: str) -> bool:
    ma, mb = SEMVER.match(a), SEMVER.match(b)
    if not ma or not mb:
        return False
    return tuple(map(int, ma.groups())) > tuple(map(int, mb.groups()))

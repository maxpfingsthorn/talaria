from __future__ import annotations

import re
from typing import Iterable

RELEASE_TAG = re.compile(r"^v(20\d\d)\.(\d{1,2})\.(\d{1,2})(?:\.(\d+))?$")
SEMVER = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def is_release(t: str) -> bool:
    return bool(RELEASE_TAG.match(t))


def key(t: str) -> tuple[int, int, int, int]:
    m = RELEASE_TAG.match(t)
    if not m:
        raise ValueError(f"not a release tag: {t!r}")
    y, mo, d, s = m.groups()
    return int(y), int(mo), int(d), int(s or 0)


def pick_candidate(git_tags: Iterable[str], registry: set[str], current: str | None,
                   excluded: set[str], floor: str) -> str | None:
    ok = [t for t in git_tags
          if is_release(t) and t in registry and t not in excluded
          and key(t) >= key(floor)
          and (current is None or key(t) > key(current))]
    return max(ok, key=key) if ok else None


def semver_newer(a: str, b: str) -> bool:
    ma, mb = SEMVER.match(a), SEMVER.match(b)
    if not ma or not mb:
        return False
    return tuple(map(int, ma.groups())) > tuple(map(int, mb.groups()))

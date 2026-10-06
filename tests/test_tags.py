from types import SimpleNamespace

import pytest

from talaria import apps, tags, upstream
from tests.fakes import FakeShell


@pytest.mark.parametrize("t,ok", [
    ("v2026.9.24", True), ("v2026.7.7.2", True), ("v2026.1.1", True),
    ("v2026.10.1", True), ("v1999.1.1", False), ("v2026.9", False),
    ("rc.1-v0.21.5", False), ("v0.21.4+canary.1", False), ("v0.21.5", False),
    ("v2026.9.24 ", False), ("v2026.123.1", False), ("abandoned-rc.3", False),
])
def test_release_pattern(t, ok):
    assert tags.is_release(t) is ok


def test_ordering_uses_numbers_and_suffix():
    ts = ["v2026.10.1", "v2026.9.24", "v2026.7.7.2", "v2026.7.7", "v2026.7.10"]
    assert sorted(ts, key=tags.key) == [
        "v2026.7.7", "v2026.7.7.2", "v2026.7.10", "v2026.9.24", "v2026.10.1"]


def test_pick_candidate():
    h = apps.get("hermes")
    git = ["v2026.6.5", "v2026.8.3", "v2026.9.24", "v2026.9.30", "v0.21.5"]
    reg = {"v2026.6.5", "v2026.8.3", "v2026.9.24", "latest"}
    pick = lambda cur, ex=set(), floor="v2026.6.5": tags.pick_candidate(h, git, reg, cur, ex, floor)
    assert pick("v2026.6.5") == "v2026.9.24"           # v2026.9.30 has no image yet
    assert pick("v2026.6.5", {"v2026.9.24"}) == "v2026.8.3"
    assert pick("v2026.9.24") is None
    assert pick(None) == "v2026.9.24"
    assert tags.pick_candidate(h, ["v2026.5.1"], {"v2026.5.1"}, None, set(), "v2026.6.5") is None


def test_tag_arg_shape():
    for t in ("v2026.9.24", "v2026.9.24.1", "v0.9.10"):
        assert tags.TAG_ARG.match(t)
    for t in ("latest", "v1.2", "v1.2.3-rc1", "v1.2.3;rm", "v" + "1" * 5 + ".1.1"):
        assert not tags.TAG_ARG.match(t)


def test_hermes_tags_via_app():
    h = apps.get("hermes")
    assert h.is_release("v2026.9.24") and not h.is_release("v0.9.10")
    assert h.tag_key("v2026.9.24.2") == (2026, 9, 24, 2)


def test_pick_candidate_orders_numerically_not_lexicographically():
    h = apps.get("hermes")
    # lexicographically "v2026.9.24" > "v2026.10.1" ('9' > '1'); numerically it's the reverse
    git = ["v2026.9.24", "v2026.10.1"]
    assert tags.pick_candidate(h, git, set(git), None, set(), "v2026.6.5") == "v2026.10.1"
    # same trap with a release's optional suffix
    git2 = ["v2026.9.24", "v2026.9.24.2"]
    assert tags.pick_candidate(h, git2, set(git2), None, set(), "v2026.6.5") == "v2026.9.24.2"


def test_pick_candidate_raises_for_a_non_release_current():
    # main's behaviour: an odd state.json with a non-release current tag fails loudly
    # rather than silently offering a possible downgrade.
    h = apps.get("hermes")
    git = ["v2026.8.3", "v2026.9.24"]
    with pytest.raises(ValueError, match="not a release tag"):
        tags.pick_candidate(h, git, set(git), "v1999.12.1", set(), "v2026.6.5")


def test_pick_candidate_uses_the_apps_order():
    h = apps.get("hermes")
    git = {"v2026.9.7": "a", "v2026.9.24": "b", "v0.9.10": "c"}
    assert tags.pick_candidate(h, git, {"v2026.9.7", "v2026.9.24", "v0.9.10"},
                               "v2026.9.7", set(), "v2026.6.5") == "v2026.9.24"


def test_semver_newer():
    assert tags.semver_newer("v0.2.0", "v0.1.9")
    assert tags.semver_newer("v0.10.0", "v0.9.0")
    assert not tags.semver_newer("v0.1.0", "v0.1.0")
    assert not tags.semver_newer("junk", "v0.1.0")


LS_REMOTE = (
    "aaa\trefs/tags/v2026.8.3\n"
    "ccc\trefs/tags/v2026.8.3^{}\n"
    "bbb\trefs/tags/v2026.9.24\n"
    "ddd\trefs/tags/rc.1-v0.21.5\n"
)


def test_git_release_tags_prefers_peeled_commit():
    sh = FakeShell().on("git", "ls-remote", "--tags", out=LS_REMOTE)
    assert upstream.git_release_tags(sh, "https://x/repo") == {
        "v2026.8.3": "ccc", "v2026.9.24": "bbb"}


def test_registry_tags_uses_podman_search():
    sh = FakeShell().on("podman", "search",
                        out="NAME TAG\ndocker.io/n/h v2026.8.3\ndocker.io/n/h latest\n")
    assert upstream.registry_tags(sh, "docker.io/n/h") == {"v2026.8.3", "latest"}
    argv = sh.calls[0]
    assert "--list-tags" in argv and "--limit" in argv and argv[-1] == "docker.io/n/h"


def test_registry_tags_tls_flag():
    sh = FakeShell().on("podman", "search", out="NAME TAG\n")
    upstream.registry_tags(sh, "localhost:5000/h", tls_verify=False)
    assert "--tls-verify=false" in sh.calls[0]


def test_latest_semver():
    sh = FakeShell().on("git", "ls-remote", out="a\trefs/tags/v0.1.0\nb\trefs/tags/v0.2.0\n"
                                                "c\trefs/tags/v0.10.0-rc1\n")
    assert upstream.latest_semver(sh, "r") == "v0.2.0"


def _ctx(git, release_allow=()):
    app = SimpleNamespace(releases=lambda c: (c is ctx) and git)
    ctx = SimpleNamespace(app=app, conf=SimpleNamespace(release_allow=release_allow))
    return ctx


def test_releases_unfiltered_without_release_allow():
    ctx = _ctx({"v1": "a", "v2": "b"})
    assert tags.releases(ctx) == {"v1": "a", "v2": "b"}


def test_releases_filters_with_release_allow():
    ctx = _ctx({"v1": "a", "v2": "b", "v3": "c"}, release_allow=("v1", "v3"))
    assert tags.releases(ctx) == {"v1": "a", "v3": "c"}


def test_releases_allow_entry_not_in_git_is_ignored():
    ctx = _ctx({"v1": "a"}, release_allow=("v1", "v9"))
    assert tags.releases(ctx) == {"v1": "a"}

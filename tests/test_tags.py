import pytest

from talaria import tags, upstream
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
    git = ["v2026.6.5", "v2026.8.3", "v2026.9.24", "v2026.9.30", "v0.21.5"]
    reg = {"v2026.6.5", "v2026.8.3", "v2026.9.24", "latest"}
    pick = lambda cur, ex=set(), floor="v2026.6.5": tags.pick_candidate(git, reg, cur, ex, floor)
    assert pick("v2026.6.5") == "v2026.9.24"           # v2026.9.30 has no image yet
    assert pick("v2026.6.5", {"v2026.9.24"}) == "v2026.8.3"
    assert pick("v2026.9.24") is None
    assert pick(None) == "v2026.9.24"
    assert tags.pick_candidate(["v2026.5.1"], {"v2026.5.1"}, None, set(), "v2026.6.5") is None


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

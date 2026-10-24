import http.client
import io
import json
import urllib.error
import urllib.request
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


def test_registry_tags_asks_for_every_tag_and_refuses_a_cut_list():
    sh = FakeShell().on("podman", "search", out="NAME TAG\n")
    upstream.registry_tags(sh, "docker.io/n/h")
    argv = sh.calls[0]
    assert argv[argv.index("--limit") + 1] == "100000"
    rows = "".join(f"docker.io/n/h t{i}\n" for i in range(upstream.TAG_LIMIT))
    sh = FakeShell().on("podman", "search", out="NAME TAG\n" + rows)
    with pytest.raises(ValueError, match="100000 or more tags"):
        upstream.registry_tags(sh, "docker.io/n/h")


def test_registry_tags_tls_flag():
    sh = FakeShell().on("podman", "search", out="NAME TAG\n")
    upstream.registry_tags(sh, "localhost:5000/h", tls_verify=False)
    assert "--tls-verify=false" in sh.calls[0]


def test_latest_semver_uses_published_release_instead_of_newer_git_tag(monkeypatch):
    sh = FakeShell().on("git", "ls-remote", out="a\trefs/tags/v0.1.0\nb\trefs/tags/v0.2.0\n"
                                                "c\trefs/tags/v0.3.0\n")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(json.dumps({
        "tag_name": "v0.2.0", "draft": False, "prerelease": False,
        "published_at": "2026-10-24T00:00:00Z"}).encode()))
    assert upstream.latest_semver(sh, "https://github.com/example/talaria") == "v0.2.0"
    assert sh.calls == []


@pytest.mark.parametrize("changes", [
    {"draft": True}, {"prerelease": True}, {"published_at": None},
    {"tag_name": "v0.3.0-rc1"}, {"tag_name": "../../bad"},
])
def test_latest_semver_never_offers_an_unpublished_or_prerelease_tag(monkeypatch, changes):
    record = {"tag_name": "v0.2.0", "draft": False, "prerelease": False,
              "published_at": "2026-10-24T00:00:00Z", **changes}
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: io.BytesIO(json.dumps(record).encode()))
    assert upstream.latest_semver(FakeShell(), "https://github.com/example/talaria") is None


def test_release_lookup_failure_never_falls_back_to_tags(monkeypatch):
    sh = FakeShell().on("git", "ls-remote", out="c\trefs/tags/v99.0.0\n")

    def offline(*args, **kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", offline)
    with pytest.raises(OSError):
        upstream.latest_semver(sh, "https://github.com/example/talaria")
    assert sh.calls == []


@pytest.mark.parametrize("repo,url", [
    ("https://github.com/example/talaria", "https://api.github.com/repos/example/talaria/releases/latest"),
    ("https://github.com/example/talaria.git/", "https://api.github.com/repos/example/talaria/releases/latest"),
    ("http://127.0.0.1:8092/talaria/", "http://127.0.0.1:8092/talaria/api/releases/latest"),
])
def test_release_lookup_uses_the_configured_repository(monkeypatch, repo, url):
    seen = []

    def get(req, timeout):
        seen.append(req.full_url)
        return io.BytesIO(json.dumps({"tag_name": "v1.2.3", "draft": False,
                                     "prerelease": False, "published_at": "2026-10-24"}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", get)
    assert upstream.latest_semver(FakeShell(), repo) == "v1.2.3"
    assert seen == [url]


@pytest.mark.parametrize("body", [b"bad json", b"[]", b"x" * (1024 * 1024 + 1)])
def test_malformed_release_metadata_fails_closed(monkeypatch, body):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body))
    with pytest.raises(ValueError):
        upstream.latest_semver(FakeShell(), "https://github.com/example/talaria")


@pytest.mark.parametrize("code", [404, 403, 500])
def test_release_lookup_http_errors(monkeypatch, code):
    def get(req, timeout):
        raise urllib.error.HTTPError(req.full_url, code, "error", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", get)
    if code == 404:
        assert upstream.latest_semver(FakeShell(), "https://github.com/example/talaria") is None
    else:
        with pytest.raises(urllib.error.HTTPError):
            upstream.latest_semver(FakeShell(), "https://github.com/example/talaria")


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


@pytest.mark.parametrize("error", [http.client.IncompleteRead(b"{"), http.client.BadStatusLine("x")])
def test_a_cut_or_malformed_response_is_a_lookup_failure(monkeypatch, error):
    def get(*args, **kwargs):
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", get)
    with pytest.raises(OSError, match="release lookup failed"):
        upstream.latest_semver(FakeShell(), "https://github.com/example/talaria")

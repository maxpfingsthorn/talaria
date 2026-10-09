# tests/test_gbrain.py
import hashlib
import json
from pathlib import Path

import pytest

from talaria import apps
from talaria.apps import gbrain as gb
from talaria.conf import load_conf
from talaria.ctx import Paths, TooLarge
from talaria.images import RevisionMismatch
from talaria.rehearse import Transient
from talaria.shell import Result
from tests.fakes import make_test_ctx

BIN = b"\x7fELF fake gbrain"
SUM = hashlib.sha256(BIN).hexdigest()
TAG = "v0.60.116.0"
GH = "https://github.com/garrytan/gbrain"


def release_json(digest=f"sha256:{SUM}", name="gbrain-linux-x64"):
    return json.dumps({"tag_name": TAG, "assets": [
        {"name": "gbrain-darwin-arm64", "digest": "sha256:" + "0" * 64},
        {"name": name, "digest": digest}]}).encode()


def gctx(tmp_path, monkeypatch, api=None, files=None, gh=None,
         version="gbrain 0.60.116.0\n"):
    ctx = make_test_ctx(tmp_path, app="gbrain")
    seen = {"get": [], "download": [], "containerfile": []}
    status, body = api or (200, release_json())

    def http_get(url, timeout=5.0):
        seen["get"].append((url, timeout))
        return status, body

    def download(url, dest, max_bytes):
        seen["download"].append((url, max_bytes))
        data = (files if files is not None else {"gbrain-linux-x64": BIN}).get(url.rsplit("/", 1)[1])
        if data is None:
            return 404
        dest.write_bytes(data)
        return 200

    def build(argv, input):
        seen["containerfile"].append((Path(argv[-1]) / "Containerfile").read_text())
        return Result(0, "sha256:built\n")
    ctx.http_get, ctx.download, ctx.seen = http_get, download, seen
    monkeypatch.setattr(gb, "which", lambda t: gh)
    ctx.sh.on("uname", "-m", out="x86_64\n")
    ctx.sh.on("podman", "build", fn=build)
    ctx.sh.on("podman", "run", out=version)
    return ctx


def test_registry_and_attributes():
    assert apps.NAMES == ("hermes", "clawvisor", "gbrain")
    a = apps.get("gbrain")
    assert (a.name, a.title, a.unit, a.container, a.quadlet_file, a.env_file, a.local_image) == (
        "gbrain", "gbrain", "gbrain.service", "gbrain", "gbrain.container", "gbrain.env",
        "localhost/gbrain")
    assert (a.default_data_dir, a.default_port, a.container_port, a.default_repo,
            a.min_release, a.backup_exclude, a.can_adopt) == (
        "~/gbrain-data", 3131, 3131, GH, "v0.60.116.0", (), False)
    assert (a.has_maintenance, a.copy_stopped, a.default_check_days,
            a.default_maintenance_time, a.prepare_summary, a.before_start_error) == (
        True, True, ("mon", "thu"), "03:30", "admin token", "the brain check before start failed")


def test_conf_defaults(tmp_path):
    c = load_conf(Paths(tmp_path, "gbrain"))
    assert (c.data_dir, c.dashboard_port, c.repo, c.min_release, c.check_days,
            c.maintenance_time, c.backup_exclude) == (
        tmp_path / "gbrain-data", 3131, GH, "v0.60.116.0", ("mon", "thu"), "03:30", ())


def test_release_tags_have_four_numbers():
    a = apps.get("gbrain")
    assert a.is_release("v0.60.116.0")
    assert not any(a.is_release(t) for t in ("v0.60.116", "v0.60.116.0-rc1", "latest", "0.60.1.0"))
    assert a.tag_key("v0.60.116.0") == (0, 60, 116, 0)
    assert a.tag_key("v0.60.9.0") < a.tag_key("v0.60.116.0") < a.tag_key("v0.61.1.0")
    with pytest.raises(ValueError, match="not a release tag: 'latest'"):
        a.tag_key("latest")


def test_releases_keep_only_release_tags(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path, app="gbrain")
    seen = {}

    def ls_remote(sh, repo):
        seen["args"] = (sh, repo)
        return {"v0.60.116.0": "a", "v0.60.117.0-rc1": "b", "latest": "c", "v0.60.9.0": "d"}
    monkeypatch.setattr(gb, "_ls_remote", ls_remote)
    assert ctx.app.releases(ctx) == {"v0.60.116.0": "a", "v0.60.9.0": "d"}
    assert seen["args"] == (ctx.sh, GH)
    assert ctx.app.published(ctx, ["v1", "v2"]) == {"v1", "v2"}
    assert ctx.app.image_refs(ctx) == ["localhost/gbrain"]


def test_github_repo_and_release_api():
    assert gb.github_repo(GH) == "garrytan/gbrain"
    assert gb.github_repo(GH + ".git") == "garrytan/gbrain"
    assert gb.github_repo(GH + "/") == "garrytan/gbrain"
    assert gb.github_repo("http://127.0.0.1:8092/gbrain") is None
    assert gb.release_api(GH, TAG) == f"https://api.github.com/repos/garrytan/gbrain/releases/tags/{TAG}"
    assert gb.release_api("http://127.0.0.1:8092/gbrain", TAG) == \
        f"http://127.0.0.1:8092/gbrain/api/releases/tags/{TAG}"


def test_base_is_pinned_by_digest():
    assert __import__("re").fullmatch(r"gcr\.io/distroless/cc-debian12@sha256:[0-9a-f]{64}", gb.BASE)


def test_fetch_verifies_the_digest_and_builds(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    rec = ctx.app.fetch(ctx, TAG, "abc")
    assert rec == {"tag": TAG, "id": "sha256:built", "digest": f"sha256:{SUM}",
                   "ref": f"build:{TAG}", "commit": "abc"}
    assert ctx.seen["get"] == [(f"https://api.github.com/repos/garrytan/gbrain/releases/tags/{TAG}", 30.0)]
    assert ctx.seen["download"] == [(f"{GH}/releases/download/{TAG}/gbrain-linux-x64", gb.MAX_BINARY)]
    (build,) = ctx.sh.called("podman", "build")
    assert build[:-1] == ["podman", "build", "-q", "--pull=missing", "--timestamp", "0",
                          "--label", "org.opencontainers.image.revision=abc",
                          "--label", f"org.opencontainers.image.version={TAG}",
                          "-t", f"localhost/gbrain:{TAG}"]
    (cf,) = ctx.seen["containerfile"]
    assert cf == (f"FROM {gb.BASE}\nCOPY --chmod=0755 gbrain /usr/local/bin/gbrain\n"
                  "EXPOSE 3131\nUSER 65532:65532\nENTRYPOINT [\"/usr/local/bin/gbrain\"]\n")
    assert ctx.sh.called("podman", "run") == [["podman", "run", "--rm", "--network=none",
                                               "sha256:built", "--version"]]
    assert not (ctx.paths.staging / f"build-{TAG}").exists()


def test_digest_mismatch_fails_verification(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, files={"gbrain-linux-x64": b"tampered"})
    with pytest.raises(RevisionMismatch, match=rf"{TAG}: gbrain-linux-x64 failed verification \(sha256"):
        ctx.app.fetch(ctx, TAG, "abc")
    assert ctx.sh.called("podman", "build") == []
    assert not (ctx.paths.staging / f"build-{TAG}").exists()


@pytest.mark.parametrize("digest", ["", "md5:abc", "sha256:XYZ"])
def test_a_missing_digest_fails_verification(tmp_path, monkeypatch, digest):
    ctx = gctx(tmp_path, monkeypatch, api=(200, release_json(digest=digest)))
    with pytest.raises(RevisionMismatch, match="publishes no sha256 digest"):
        ctx.app.fetch(ctx, TAG, "abc")


@pytest.mark.parametrize("api,files,msg", [
    ((404, b""), None, "not published yet \\(release API answered 404\\)"),
    ((0, b""), None, "not published yet \\(release API answered nothing\\)"),
    ((200, b"<html>"), None, "answered no JSON"),
    ((200, release_json(name="gbrain-linux-arm64")), None, "release assets of v0.60.116.0 are not published yet"),
    (None, {}, "release assets of v0.60.116.0 are not published yet"),
])
def test_unpublished_or_unreachable_release_is_transient(tmp_path, monkeypatch, api, files, msg):
    ctx = gctx(tmp_path, monkeypatch, api=api, files=files)
    with pytest.raises(Transient, match=msg):
        ctx.app.fetch(ctx, TAG, "abc")


def test_rate_limited_release_api_is_transient(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, api=(403, b'{"message": "API rate limit exceeded"}'))
    with pytest.raises(Transient, match="answered 403"):
        ctx.app.fetch(ctx, TAG, "abc")


def test_oversized_asset_is_permanent(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)

    def big(url, dest, max_bytes):
        raise TooLarge("too big")
    ctx.download = big
    with pytest.raises(RevisionMismatch, match="download limit"):
        ctx.app.fetch(ctx, TAG, "abc")


def test_unsupported_architecture(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    ctx.sh.on("uname", "-m", out="aarch64\n")
    with pytest.raises(RevisionMismatch, match="unsupported architecture: aarch64"):
        ctx.app.fetch(ctx, TAG, "abc")
    assert ctx.seen["get"] == []


@pytest.mark.parametrize("out,ok", [
    ("gbrain 0.60.116.0\n", True),
    ("UPGRADE_AVAILABLE 0.60.116.0 0.60.120.0\nRun: gbrain self-upgrade\ngbrain 0.60.116.0\n", True),
    ("gbrain 0.60.115.0\n", False),
    ("", False),
])
def test_the_binary_must_report_the_tag(tmp_path, monkeypatch, out, ok):
    ctx = gctx(tmp_path, monkeypatch, version=out)
    if ok:
        assert ctx.app.fetch(ctx, TAG, "abc")["tag"] == TAG
    else:
        with pytest.raises(RevisionMismatch, match="the binary reports version"):
            ctx.app.fetch(ctx, TAG, "abc")


def test_attestation_runs_when_gh_is_logged_in(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, gh="/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status").on("gh", "attestation", "verify")
    ctx.app.fetch(ctx, TAG, "abc")
    (v,) = ctx.sh.called("gh", "attestation")
    assert v[:3] == ["gh", "attestation", "verify"] and v[4:] == ["-R", "garrytan/gbrain"]
    assert v[3].endswith(f"build-{TAG}/gbrain")
    assert ctx.sh.timeouts[ctx.sh.calls.index(v)] == 300


def test_attestation_failure_is_permanent(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, gh="/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status")
    ctx.sh.on("gh", "attestation", rc=1, err="Verification failed\nno matching attestations\n")
    with pytest.raises(RevisionMismatch, match=r"failed verification \(attestation: no matching attestations\)"):
        ctx.app.fetch(ctx, TAG, "abc")
    assert ctx.sh.called("podman", "build") == []


def test_attestation_skipped_without_a_gh_login(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, gh="/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status", rc=1)
    ctx.app.fetch(ctx, TAG, "abc")
    assert ctx.sh.called("gh", "attestation") == []


def test_attestation_unavailable_reasons(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, gh=None)
    assert gb.attestation_unavailable(ctx) == "gh is not installed"
    monkeypatch.setattr(gb, "which", lambda t: "/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status", rc=1)
    assert gb.attestation_unavailable(ctx) == "gh is not logged in for this account"
    ctx.sh.on("gh", "auth", "status", rc=0)
    assert gb.attestation_unavailable(ctx) is None
    ctx.conf.repo = "http://127.0.0.1:8092/gbrain"
    assert gb.attestation_unavailable(ctx) == "the repository is not on GitHub"


def test_reacquire_fetches_again(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    ctx.app.reacquire(ctx, {"tag": TAG, "id": "sha256:old", "commit": "abc"})
    assert len(ctx.sh.called("podman", "build")) == 1


@pytest.mark.parametrize("answer,reason", [
    ((200, b'{"status":"ok","version":"0.60.116.0","engine":"pglite"}'), None),
    ((503, b'{"status":"unavailable"}'), "/health answered 503"),
    ((0, b""), "/health answered nothing"),
    ((200, b"<html>"), "/health did not answer JSON"),
    ((200, b'{"status":"degraded"}'), "/health reports status degraded"),
    ((200, b"[]"), "/health reports status ?"),
])
def test_health(tmp_path, answer, reason):
    ctx = make_test_ctx(tmp_path, app="gbrain")
    urls = []
    ctx.http_get = lambda url, timeout=5.0: (urls.append((url, timeout)), answer)[1]
    assert ctx.app.health(ctx) == reason
    assert urls == [("http://127.0.0.1:3131/health", 5.0)]

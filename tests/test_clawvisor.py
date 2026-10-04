import hashlib

import pytest

from talaria import apps, rehearse
from talaria.images import RevisionMismatch
from tests.fakes import make_test_ctx

BIN = b"\x7fELF fake clawvisor"
SUM = hashlib.sha256(BIN).hexdigest()


def cctx(tmp_path, files):
    ctx = make_test_ctx(tmp_path, app="clawvisor")

    def download(url, dest, max_bytes):
        name = url.rsplit("/", 1)[1]
        if name not in files:
            return 404
        dest.write_bytes(files[name])
        return 200
    ctx.download = download
    ctx.sh.on("uname", "-m", out="x86_64\n")
    ctx.sh.on("podman", "build", out="sha256:built\n")
    ctx.sh.on("podman", "run", out="clawvisor-server 0.9.10\n")
    return ctx


def test_releases_are_plain_semver_tags(tmp_path, monkeypatch):
    from talaria.apps import clawvisor as cv
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    monkeypatch.setattr(cv, "_ls_remote", lambda sh, repo: {
        "v0.9.10": "a", "v0.9.11-rc1": "b", "latest": "c", "v0.9.9": "d"})
    assert ctx.app.releases(ctx) == {"v0.9.10": "a", "v0.9.9": "d"}


def test_fetch_verifies_checksum_and_builds(tmp_path):
    ctx = cctx(tmp_path, {"checksums.txt": f"{SUM}  clawvisor-server-linux-amd64\n".encode(),
                          "clawvisor-server-linux-amd64": BIN})
    rec = ctx.app.fetch(ctx, "v0.9.10", "abc")
    assert rec == {"tag": "v0.9.10", "id": "sha256:built", "digest": f"sha256:{SUM}",
                   "ref": "build:v0.9.10", "commit": "abc"}
    build = ctx.sh.called("podman", "build")[0]
    assert "--label" in build and "org.opencontainers.image.revision=abc" in build
    assert "localhost/clawvisor:v0.9.10" in build


def test_checksum_mismatch_is_permanent(tmp_path):
    ctx = cctx(tmp_path, {"checksums.txt": f"{'0' * 64}  clawvisor-server-linux-amd64\n".encode(),
                          "clawvisor-server-linux-amd64": BIN})
    with pytest.raises(RevisionMismatch, match="checksum"):
        ctx.app.fetch(ctx, "v0.9.10", "abc")
    assert ctx.sh.called("podman", "build") == []


def test_missing_assets_are_transient(tmp_path):
    ctx = cctx(tmp_path, {})
    with pytest.raises(rehearse.Transient, match="release assets of v0.9.10 are not published yet"):
        ctx.app.fetch(ctx, "v0.9.10", "abc")


def test_binary_version_must_match_the_tag(tmp_path):
    ctx = cctx(tmp_path, {"checksums.txt": f"{SUM}  clawvisor-server-linux-amd64\n".encode(),
                          "clawvisor-server-linux-amd64": BIN})
    ctx.sh.on("podman", "run", out="clawvisor-server 0.9.9\n")
    with pytest.raises(RevisionMismatch, match="reports version 0.9.9"):
        ctx.app.fetch(ctx, "v0.9.10", "abc")

import json

import pytest

from talaria import images
from tests.fakes import make_test_ctx


def inspect_json(rev="c0ffee", digest="sha256:d1", iid="sha256:i1", size=100):
    return json.dumps([{"Id": iid, "Digest": digest, "Size": size,
                        "Labels": {"org.opencontainers.image.revision": rev},
                        "RepoDigests": [f"docker.io/nousresearch/hermes-agent@{digest}"]}])


def test_pull_verify_returns_record(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "pull").on("podman", "image", "inspect", out=inspect_json())
    rec = images.pull_verify(ctx, "v2026.8.3", "c0ffee")
    assert rec == {"tag": "v2026.8.3", "id": "sha256:i1", "digest": "sha256:d1",
                   "ref": "docker.io/nousresearch/hermes-agent@sha256:d1"}
    assert ctx.sh.called("podman", "pull")[0][-1] == "docker.io/nousresearch/hermes-agent:v2026.8.3"


def test_pull_verify_rejects_wrong_revision(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "pull").on("podman", "image", "inspect", out=inspect_json(rev="evil"))
    with pytest.raises(images.RevisionMismatch):
        images.pull_verify(ctx, "v2026.8.3", "c0ffee")


def test_pull_verify_tls_flag(tmp_path):
    ctx = make_test_ctx(tmp_path, registry_tls_verify=False)
    ctx.sh.on("podman", "pull").on("podman", "image", "inspect", out=inspect_json())
    images.pull_verify(ctx, "v2026.8.3", "c0ffee")
    assert "--tls-verify=false" in ctx.sh.called("podman", "pull")[0]


def test_ensure_repulls_missing_by_digest(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "image", "exists", rc=1).on("podman", "pull")
    images.ensure(ctx, {"id": "sha256:i1", "ref": "x/h@sha256:d1"})
    assert ctx.sh.called("podman", "pull")[0][-1] == "x/h@sha256:d1"


def test_ensure_missing_local_image_raises(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "image", "exists", rc=1)
    with pytest.raises(images.ImageMissing):
        images.ensure(ctx, {"id": "sha256:i1", "ref": None})


def test_retag(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "tag")
    images.retag(ctx, "current", {"id": "sha256:i1"})
    assert ctx.sh.calls[-1] == ["podman", "tag", "sha256:i1", "localhost/hermes-agent:current"]


def test_prune_keeps_listed(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "images", out=json.dumps([{"Id": "a"}, {"Id": "b"}, {"Id": "c"}]))
    ctx.sh.on("podman", "rmi")
    removed = images.prune(ctx, [{"id": "a"}, None, {"id": "c"}])
    assert removed == ["b"]
    assert ctx.sh.called("podman", "rmi") == [["podman", "rmi", "b"]]


@pytest.mark.parametrize("text,tag", [
    ("Hermes Agent v0.16.0 (2026.6.5)\n", "v2026.6.5"),
    ("Hermes Agent v0.21.0 (2026.7.7.2)", "v2026.7.7.2"),
    ("garbage", None),
])
def test_version_tag(text, tag):
    assert images.version_tag(text) == tag


def test_local_record(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "image", "inspect", out=json.dumps([{"Id": "sha256:loc", "RepoDigests": []}]))
    ctx.sh.on("podman", "run", out="Hermes Agent v0.16.0 (2026.6.5)\n")
    assert images.local_record(ctx, "sha256:loc") == {
        "tag": "v2026.6.5", "id": "sha256:loc", "ref": None, "digest": None}

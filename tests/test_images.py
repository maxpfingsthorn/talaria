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


def test_pull_verify_exact_commands(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "pull").on("podman", "image", "inspect", out=inspect_json())
    images.pull_verify(ctx, "v2026.8.3", "c0ffee")
    assert list(zip(ctx.sh.calls, ctx.sh.timeouts)) == [
        (["podman", "pull", "-q", "docker.io/nousresearch/hermes-agent:v2026.8.3"], 3600),
        (["podman", "image", "inspect", "docker.io/nousresearch/hermes-agent:v2026.8.3"], None)]


def test_pull_verify_label_under_config(tmp_path):
    ctx = make_test_ctx(tmp_path)
    info = json.loads(inspect_json())
    info[0]["Config"] = {"Labels": info[0].pop("Labels")}
    ctx.sh.on("podman", "pull").on("podman", "image", "inspect", out=json.dumps(info))
    assert images.pull_verify(ctx, "v2026.8.3", "c0ffee")["id"] == "sha256:i1"


def test_pull_verify_mismatch_message(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "pull").on("podman", "image", "inspect", out=inspect_json(rev="evil"))
    with pytest.raises(images.RevisionMismatch) as e:
        images.pull_verify(ctx, "v2026.8.3", "c0ffee")
    assert str(e.value) == "v2026.8.3: image revision 'evil' is not the tag's commit c0ffee"


def test_size(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "image", "inspect", out=inspect_json(size=123))
    assert images.size(ctx, {"id": "sha256:i1"}) == 123
    assert ctx.sh.calls == [["podman", "image", "inspect", "sha256:i1"]]


def test_size_missing_field(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "image", "inspect", out='[{"Id": "x"}]')
    assert images.size(ctx, {"id": "x"}) == 0


def test_exists_exact(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "image", "exists", rc=0)
    assert images.exists(ctx, {"id": "sha256:a"}) is True
    ctx.sh.on("podman", "image", "exists", rc=1)
    assert images.exists(ctx, {"id": "sha256:a"}) is False
    assert ctx.sh.calls[0] == ["podman", "image", "exists", "sha256:a"]


def test_ensure_present_does_nothing(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "image", "exists", rc=0)
    images.ensure(ctx, {"id": "a", "ref": "x@d"})
    assert len(ctx.sh.calls) == 1


def test_ensure_exact_pull_and_tls(tmp_path):
    ctx = make_test_ctx(tmp_path, registry_tls_verify=False)
    ctx.sh.on("podman", "image", "exists", rc=1).on("podman", "pull")
    images.ensure(ctx, {"id": "a", "ref": "x@d"})
    assert list(zip(ctx.sh.calls, ctx.sh.timeouts))[1] == (
        ["podman", "pull", "-q", "--tls-verify=false", "x@d"], 3600)


def test_ensure_missing_local_message(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "image", "exists", rc=1)
    with pytest.raises(images.ImageMissing) as e:
        images.ensure(ctx, {"id": "sha256:0123456789abcdef0123", "ref": None})
    assert str(e.value) == "local image sha256:0123456789ab is gone and cannot be pulled again"


def test_prune_exact_listing_and_prefix(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "images", out=json.dumps([{"Id": "aaa"}, {"Id": "bbb"}]))
    ctx.sh.on("podman", "rmi", rc=1)          # an image in use: ignored
    assert images.prune(ctx, [{"id": "sha256:aaa"}]) == ["bbb"]
    assert ctx.sh.calls[0] == ["podman", "images", "--format", "json", "--filter",
                               "reference=docker.io/nousresearch/hermes-agent"]


def test_prune_empty_listing(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "images", out="")
    assert images.prune(ctx, []) == []


def test_local_record_exact(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "image", "inspect", out=json.dumps(
        [{"Id": "sha256:loc", "RepoDigests": ["other/x@sha256:1",
                                               "docker.io/nousresearch/hermes-agent@sha256:2"]}]))
    ctx.sh.on("podman", "run", out="Hermes Agent v0.16.0 (2026.6.5)\n")
    assert images.local_record(ctx, "abc") == {
        "tag": "v2026.6.5", "id": "sha256:loc",
        "ref": "docker.io/nousresearch/hermes-agent@sha256:2", "digest": "sha256:2"}
    assert list(zip(ctx.sh.calls, ctx.sh.timeouts)) == [
        (["podman", "image", "inspect", "abc"], None),
        (["podman", "run", "--rm", "--network=none", "--entrypoint",
          "/opt/hermes/.venv/bin/hermes", "sha256:loc", "--version"], 300)]


def test_local_record_without_repo_digests(tmp_path):
    ctx = make_test_ctx(tmp_path)
    ctx.sh.on("podman", "image", "inspect", out='[{"Id": "sha256:loc"}]')
    ctx.sh.on("podman", "run", out="Hermes Agent v0.16.0 (2026.6.5)\n")
    assert images.local_record(ctx, "sha256:loc")["ref"] is None

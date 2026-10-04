from __future__ import annotations

import json
import re

_VERSION = re.compile(r"\((\d{4})\.(\d{1,2})\.(\d{1,2})(?:\.(\d+))?\)")


class RevisionMismatch(Exception):
    pass


class ImageMissing(Exception):
    pass


def _tls(ctx) -> list[str]:
    return [] if ctx.conf.registry_tls_verify else ["--tls-verify=false"]


def _inspect(ctx, ref: str) -> dict:
    return json.loads(ctx.sh.run(["podman", "image", "inspect", ref]).stdout)[0]


def pull_verify(ctx, tag: str, commit: str) -> dict:
    ref = f"{ctx.conf.image}:{tag}"
    ctx.sh.run(["podman", "pull", "-q", *_tls(ctx), ref], timeout=3600)
    info = _inspect(ctx, ref)
    labels = info.get("Labels") or (info.get("Config") or {}).get("Labels") or {}
    rev = labels.get("org.opencontainers.image.revision")
    if rev != commit:
        raise RevisionMismatch(f"{tag}: image revision {rev!r} is not the tag's commit {commit}")
    digest = info["Digest"]
    return {"tag": tag, "id": info["Id"], "digest": digest,
            "ref": f"{ctx.conf.image}@{digest}"}


def size(ctx, rec: dict) -> int:
    return int(_inspect(ctx, rec["id"]).get("Size", 0))


def exists(ctx, rec: dict) -> bool:
    return ctx.sh.run(["podman", "image", "exists", rec["id"]], check=False).returncode == 0


def ensure(ctx, rec: dict) -> None:
    if not exists(ctx, rec):
        ctx.app.reacquire(ctx, rec)


def retag(ctx, name: str, rec: dict) -> None:
    ctx.sh.run(["podman", "tag", rec["id"], f"{ctx.app.local_image}:{name}"])


def prune(ctx, keep: list) -> list[str]:
    keep_ids = {r["id"] for r in keep if r}
    removed = []
    seen = set()
    for ref in ctx.app.image_refs(ctx):
        if not ref:   # never list/prune with an empty filter: that would hit every image
            continue
        listed = json.loads(ctx.sh.run(["podman", "images", "--format", "json",
                                        "--filter", f"reference={ref}"]).stdout or "[]")
        for img in listed:
            iid = img["Id"]  # `podman images` omits the sha256: prefix that `inspect` has
            if iid in seen:
                continue
            seen.add(iid)
            if iid in keep_ids or f"sha256:{iid}" in keep_ids:
                continue
            ctx.sh.run(["podman", "rmi", iid], check=False)
            removed.append(iid)
    return removed


def version_tag(text: str) -> str | None:
    m = _VERSION.search(text)
    if not m:
        return None
    y, mo, d, s = m.groups()
    return f"v{y}.{mo}.{d}" + (f".{s}" if s else "")


def local_record(ctx, image_id: str) -> dict:
    info = _inspect(ctx, image_id)
    out = ctx.sh.run(["podman", "run", "--rm", "--network=none",
                      "--entrypoint", "/opt/hermes/.venv/bin/hermes", info["Id"],
                      "--version"], timeout=300).stdout
    rec = {"tag": version_tag(out), "id": info["Id"], "ref": None, "digest": None}
    for rd in info.get("RepoDigests") or []:
        if rd.startswith(f"{ctx.conf.image}@"):
            rec["ref"], rec["digest"] = rd, rd.split("@", 1)[1]
    return rec

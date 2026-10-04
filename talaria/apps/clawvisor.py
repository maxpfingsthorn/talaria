from __future__ import annotations

import hashlib
import re
import shutil
from string import Template

from talaria.apps.base import App
from talaria.images import RevisionMismatch
from talaria.state import ensure_dir
from talaria.upstream import _ls_remote

TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
BASE = ("gcr.io/distroless/static-debian12@sha256:"
        "afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab")
ARCH = {"x86_64": "amd64", "aarch64": "arm64"}
MAX_BINARY = 300 * 1024 * 1024


def _get(ctx, url: str, dest, max_bytes: int, tag: str, label: str) -> int:
    """ctx.download, with an oversized asset treated as a bad publish (Permanent) rather
    than a transient network error: a release's assets are immutable once published, so
    retrying tomorrow would just re-download the same oversized file and crash again."""
    try:
        return ctx.download(url, dest, max_bytes)
    except OSError as e:
        raise RevisionMismatch(
            f"{tag}: {label} exceeds the {max_bytes}-byte download limit ({e})") from None


class Clawvisor(App):
    name = "clawvisor"
    title = "Clawvisor"
    unit = "clawvisor.service"
    container = "clawvisor"
    quadlet_file = "clawvisor.container"
    env_file = "clawvisor.env"
    local_image = "localhost/clawvisor"
    default_data_dir = "~/clawvisor-data"
    default_port = 25297
    container_port = 25297
    default_image = ""
    default_repo = "https://github.com/clawvisor/clawvisor"
    min_release = "v0.9.9"
    backup_exclude = ()
    can_adopt = False

    def is_release(self, tag: str) -> bool:
        return bool(TAG.match(tag))

    def tag_key(self, tag: str) -> tuple:
        m = TAG.match(tag)
        if not m:
            raise ValueError(f"not a release tag: {tag!r}")
        return tuple(int(x) for x in m.groups())

    def releases(self, ctx) -> dict[str, str]:
        return {t: c for t, c in _ls_remote(ctx.sh, ctx.conf.hermes_repo).items()
                if self.is_release(t)}

    def published(self, ctx, tags) -> set[str]:
        return set(tags)

    def image_refs(self, ctx) -> list[str]:
        return [self.local_image]

    def fetch(self, ctx, tag: str, commit: str) -> dict:
        from talaria.rehearse import Transient
        arch = ARCH[ctx.sh.run(["uname", "-m"]).stdout.strip()]
        asset = f"clawvisor-server-linux-{arch}"
        base = f"{ctx.conf.hermes_repo}/releases/download/{tag}"
        work = ctx.paths.staging / f"build-{tag}"
        shutil.rmtree(work, ignore_errors=True)
        ensure_dir(ctx.paths.staging)
        work.mkdir(mode=0o700)
        try:
            if _get(ctx, f"{base}/checksums.txt", work / "checksums.txt", 1 << 20,
                    tag, "checksums.txt") != 200 \
                    or _get(ctx, f"{base}/{asset}", work / "clawvisor-server", MAX_BINARY,
                            tag, asset) != 200:
                raise Transient(f"release assets of {tag} are not published yet")
            want = {l.split()[-1]: l.split()[0] for l in
                    (work / "checksums.txt").read_text().splitlines() if len(l.split()) == 2}
            got = hashlib.sha256((work / "clawvisor-server").read_bytes()).hexdigest()
            if want.get(asset) != got:
                raise RevisionMismatch(f"{tag}: checksum of {asset} does not match checksums.txt")
            (work / "Containerfile").write_text(Template(
                (ctx.paths.templates_dir / "clawvisor.Containerfile").read_text()
            ).substitute(base=BASE))
            iid = ctx.sh.run(["podman", "build", "-q", "--pull=missing",
                              "--label", f"org.opencontainers.image.revision={commit}",
                              "--label", f"org.opencontainers.image.version={tag}",
                              "-t", f"{self.local_image}:{tag}", str(work)],
                             timeout=1800).stdout.strip().splitlines()[-1]
        finally:
            shutil.rmtree(work, ignore_errors=True)
        out = ctx.sh.run(["podman", "run", "--rm", "--network=none", iid, "--version"],
                         timeout=120).stdout.split()
        if out[-1:] != [tag[1:]]:
            raise RevisionMismatch(f"{tag}: the binary reports version {out[-1] if out else '?'}")
        return {"tag": tag, "id": iid, "digest": f"sha256:{got}", "ref": f"build:{tag}",
                "commit": commit}

    def reacquire(self, ctx, rec: dict) -> None:
        self.fetch(ctx, rec["tag"], rec.get("commit") or "")


APP = Clawvisor()

"""gbrain (github.com/garrytan/gbrain) as a Talaria app (spec 2026-10-09). No container
image is published: Talaria downloads the release binary, verifies it (the release API's
sha256 digest; `gh attestation verify` when gh is logged in) and builds a local image."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from string import Template

from talaria.apps.base import App
from talaria.ctx import TooLarge
from talaria.images import RevisionMismatch
from talaria.state import ensure_dir
from talaria.upstream import _ls_remote

TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)\.(\d+)$")
BASE = ("gcr.io/distroless/cc-debian12@sha256:"
        "6dc8478fd8e790ae427aeff51e588573dec83b13adb7e5d10505a717927009b5")
ASSET = "gbrain-linux-x64"
MAX_BINARY = 512 * 1024 * 1024
which = shutil.which


def github_repo(repo: str) -> str | None:
    """"owner/name" of an https://github.com/owner/name URL, else None."""
    m = re.match(r"^https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?$", repo)
    return m[1] if m else None


def release_api(repo: str, tag: str) -> str:
    """Where a release's JSON (assets with their sha256 digest) lives: GitHub's REST API,
    or `<repo>/api/releases/tags/<tag>` elsewhere (the e2e fake release's layout)."""
    gh = github_repo(repo)
    if gh:
        return f"https://api.github.com/repos/{gh}/releases/tags/{tag}"
    return f"{repo}/api/releases/tags/{tag}"


def attestation_unavailable(ctx) -> str | None:
    """Why `gh attestation verify` cannot run for this account, or None if it can."""
    if not github_repo(ctx.conf.repo):
        return "the repository is not on GitHub"
    if which("gh") is None:
        return "gh is not installed"
    if ctx.sh.run(["gh", "auth", "status"], check=False, timeout=60).returncode != 0:
        return "gh is not logged in for this account"
    return None


class Gbrain(App):
    name = "gbrain"
    title = "gbrain"
    unit = "gbrain.service"
    container = "gbrain"
    quadlet_file = "gbrain.container"
    env_file = "gbrain.env"
    local_image = "localhost/gbrain"
    default_data_dir = "~/gbrain-data"
    default_port = 3131
    container_port = 3131
    default_image = ""
    default_repo = "https://github.com/garrytan/gbrain"
    min_release = "v0.60.116.0"
    backup_exclude = ()
    can_adopt = False
    prepare_summary = "admin token"
    before_start_error = "the brain check before start failed"
    copy_stopped = True               # PGLite's files are consistent only at rest (spike §8)
    has_maintenance = True            # gbrain dream
    default_check_days = ("mon", "thu")   # several releases a day (spec §5.1)
    default_maintenance_time = "03:30"

    def is_release(self, tag: str) -> bool:
        return bool(TAG.match(tag))

    def tag_key(self, tag: str) -> tuple:
        m = TAG.match(tag)
        if not m:
            raise ValueError(f"not a release tag: {tag!r}")
        return tuple(int(x) for x in m.groups())

    def releases(self, ctx) -> dict[str, str]:
        return {t: c for t, c in _ls_remote(ctx.sh, ctx.conf.repo).items()
                if self.is_release(t)}

    def published(self, ctx, tags) -> set[str]:
        return set(tags)          # assets are checked in fetch(); missing ones are Transient

    def image_refs(self, ctx) -> list[str]:
        return [self.local_image]

    def _digest(self, ctx, tag: str) -> str:
        """The sha256 hex the release publishes for ASSET."""
        from talaria.rehearse import Transient
        code, body = ctx.http_get(release_api(ctx.conf.repo, tag), 30.0)
        if code != 200:
            raise Transient(f"the release {tag} is not published yet (release API answered "
                            f"{code or 'nothing'})")
        try:
            rel = json.loads(body)
        except ValueError:
            raise Transient(f"the release API answered no JSON for {tag}") from None
        assets = rel.get("assets") if isinstance(rel, dict) else None
        asset = next((a for a in assets or [] if isinstance(a, dict) and a.get("name") == ASSET),
                     None)
        if asset is None:
            raise Transient(f"release assets of {tag} are not published yet")
        digest = str(asset.get("digest") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise RevisionMismatch(f"{tag}: {ASSET} failed verification (the release publishes "
                                   "no sha256 digest)")
        return digest[len("sha256:"):]

    def fetch(self, ctx, tag: str, commit: str) -> dict:
        from talaria.rehearse import Transient
        machine = ctx.sh.run(["uname", "-m"]).stdout.strip()
        if machine != "x86_64":
            raise RevisionMismatch(f"unsupported architecture: {machine}")
        want = self._digest(ctx, tag)
        work = ctx.paths.staging / f"build-{tag}"
        shutil.rmtree(work, ignore_errors=True)
        ensure_dir(ctx.paths.staging)
        work.mkdir(mode=0o700)
        try:
            try:
                code = ctx.download(f"{ctx.conf.repo}/releases/download/{tag}/{ASSET}",
                                    work / "gbrain", MAX_BINARY)
            except TooLarge as e:
                raise RevisionMismatch(f"{tag}: {ASSET} exceeds the {MAX_BINARY}-byte download "
                                       f"limit ({e})") from None
            if code != 200:
                raise Transient(f"release assets of {tag} are not published yet")
            got = hashlib.sha256((work / "gbrain").read_bytes()).hexdigest()
            if got != want:
                raise RevisionMismatch(f"{tag}: {ASSET} failed verification (sha256 {got[:12]}… "
                                       f"is not the published {want[:12]}…)")
            if attestation_unavailable(ctx) is None:
                r = ctx.sh.run(["gh", "attestation", "verify", str(work / "gbrain"), "-R",
                                github_repo(ctx.conf.repo)], check=False, timeout=300)
                if r.returncode != 0:
                    lines = (r.stderr or r.stdout).strip().splitlines()
                    raise RevisionMismatch(f"{tag}: {ASSET} failed verification (attestation: "
                                           f"{lines[-1] if lines else f'exit {r.returncode}'})")
            (work / "Containerfile").write_text(Template(
                (ctx.paths.templates_dir / "gbrain.Containerfile").read_text()
            ).substitute(base=BASE))
            iid = ctx.sh.run(["podman", "build", "-q", "--pull=missing", "--timestamp", "0",
                              "--label", f"org.opencontainers.image.revision={commit}",
                              "--label", f"org.opencontainers.image.version={tag}",
                              "-t", f"{self.local_image}:{tag}", str(work)],
                             timeout=1800).stdout.strip().splitlines()[-1]
        finally:
            shutil.rmtree(work, ignore_errors=True)
        out = ctx.sh.run(["podman", "run", "--rm", "--network=none", iid, "--version"],
                         timeout=120).stdout
        m = re.search(r"\bgbrain (\d+(?:\.\d+){3})\b", out)
        if not m or m[1] != tag[1:]:
            raise RevisionMismatch(f"{tag}: the binary reports version {m[1] if m else '?'}")
        return {"tag": tag, "id": iid, "digest": f"sha256:{got}", "ref": f"build:{tag}",
                "commit": commit}

    def reacquire(self, ctx, rec: dict) -> None:
        self.fetch(ctx, rec["tag"], rec.get("commit") or "")

    def health(self, ctx) -> str | None:
        code, body = ctx.http_get(f"http://{ctx.conf.bind_ip}:{ctx.conf.dashboard_port}/health",
                                  5.0)
        if code != 200:
            return f"/health answered {code or 'nothing'}"
        try:
            r = json.loads(body)
        except ValueError:
            return "/health did not answer JSON"
        status = r.get("status") if isinstance(r, dict) else "?"
        return None if status == "ok" else f"/health reports status {status}"


APP = Gbrain()

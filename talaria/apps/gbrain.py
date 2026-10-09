"""gbrain (github.com/garrytan/gbrain) as a Talaria app (spec 2026-10-09). No container
image is published: Talaria downloads the release binary, verifies it (the release API's
sha256 digest; `gh attestation verify` when gh is logged in) and builds a local image."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets as _secrets
import shutil
import subprocess
from pathlib import Path
from string import Template

from talaria.apps.base import App
from talaria.conf import parse_kv, write_env_value
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
UNS = "--userns=keep-id:uid=65532,gid=65532"
ONEOFF = "talaria-gbrain-oneoff"
MARKER = "talaria rehearsal marker"
MARKER_TEXT = (f"{MARKER}: Talaria recalls this page to check that a new gbrain release "
               "can read the brain.")
SCHEMA_FILE = ".talaria-schema"
INIT_FILE = ".talaria-init"
TOKEN_KEY = "GBRAIN_ADMIN_BOOTSTRAP_TOKEN"
NEEDS_URL = ("gbrain needs dashboard.public_url in talaria.conf: the https address MCP "
             "connectors reach it at, e.g. https://<host>.<tailnet>.ts.net:8443 (README: gbrain)")


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

def oneoff(ctx, image: str, data, args: list[str], *, network: bool = False,
           env: bool = False, timeout: float = 300):
    """`gbrain <args>` once, in a throwaway container on `data`. Offline unless `network`;
    the app env file (provider keys) only with `env`. Never raises on a non-zero exit."""
    ctx.sh.run(["podman", "rm", "-f", ONEOFF], check=False, timeout=120)
    try:
        return ctx.sh.run(["podman", "run", "--rm", "--name", ONEOFF, "--read-only", UNS,
                           *([] if network else ["--network=none"]),
                           "-v", f"{data}:/data:Z", "-e", "HOME=/data", "-e", "GBRAIN_HOME=/data",
                           *(["--env-file", str(ctx.paths.app_env)] if env else []),
                           image, *args], check=False, timeout=timeout)
    finally:
        ctx.sh.run(["podman", "rm", "-f", ONEOFF], check=False, timeout=120)


def tail(r, n: int = 20) -> str:
    return "\n".join((r.stdout + r.stderr).strip().splitlines()[-n:])[-2000:]


def last_line(r) -> str:
    lines = [l.strip() for l in (r.stderr or r.stdout or "").splitlines() if l.strip()]
    return lines[-1][:300] if lines else f"exit {r.returncode}"


def parse_schema(out: str) -> int:
    """The brain's schema version from `gbrain doctor --json`; ValueError (the reason)
    unless the schema_version check is ok. Other checks (keyless warnings) do not count."""
    starts = [i for i in (out.find("{"), out.find("[")) if i >= 0]
    start, end = min(starts, default=-1), max(out.rfind("}"), out.rfind("]"))
    if start < 0 or end < start:
        raise ValueError("doctor printed no JSON")
    try:
        doc = json.loads(out[start:end + 1])
    except ValueError:
        raise ValueError("doctor printed no valid JSON") from None
    checks = doc.get("checks") if isinstance(doc, dict) else None
    for c in checks if isinstance(checks, list) else []:
        if isinstance(c, dict) and c.get("name") == "schema_version":
            if c.get("status") != "ok":
                raise ValueError(f"schema_version is {c.get('status')}: {c.get('message')}")
            m = re.search(r"Version (\d+)", str(c.get("message") or ""))
            if not m:
                raise ValueError(f"schema_version names no version: {c.get('message')}")
            return int(m[1])
    raise ValueError("doctor reports no schema_version check")


def write_schema(data_dir, v: int) -> None:
    (Path(data_dir) / SCHEMA_FILE).write_text(f"{v}\n")


def _redact(ctx, text: str) -> str:
    """Values from the app env file (provider keys, the admin token) never reach a message."""
    try:
        env = parse_kv(ctx.paths.app_env.read_text())
    except (OSError, UnicodeDecodeError):
        env = {}
    for v in sorted((v for v in env.values() if len(v) >= 4), key=len, reverse=True):
        text = text.replace(v, "***")
    return text


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

    def quadlet_vars(self, ctx) -> dict:
        if not ctx.conf.dashboard_public_url:
            raise ValueError(NEEDS_URL)
        return {"public_url": ctx.conf.dashboard_public_url,
                "container_port": self.container_port}

    def prepare(self, ctx) -> list[str]:
        if not ctx.conf.dashboard_public_url:
            raise ValueError(NEEDS_URL)
        data = ctx.conf.data_dir
        data.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(data, 0o700)              # some PGLite files are 0644 (spike §2)
        env = parse_kv(ctx.paths.app_env.read_text()) if ctx.paths.app_env.exists() else {}
        if env.get(TOKEN_KEY):
            return []
        write_env_value(ctx.paths.app_env, TOKEN_KEY, _secrets.token_urlsafe(32))
        return [f"admin token generated in {ctx.paths.app_env} ({TOKEN_KEY})"]

    def setup_notes(self, ctx) -> list[str]:
        why = attestation_unavailable(ctx)
        if why is None or not github_repo(ctx.conf.repo):
            return []
        return [f"gbrain downloads are checked against the release's sha256 digest only "
                f"({why}); install gh and run `gh auth login` as this account to also verify "
                "their build provenance"]

    def initialize(self, ctx) -> list[str]:
        """First run: a keyless PGLite brain, gbrain's own update checks off, Talaria's
        marker page for the rehearsal's recall, the schema version."""
        data = ctx.conf.data_dir
        if (data / INIT_FILE).exists():
            return []
        image = f"{self.local_image}:current"
        steps = [] if (data / ".gbrain/config.json").exists() else [["init", "--pglite"]]
        steps += [["config", "set", "self_upgrade.mode", "off"],
                  ["remember", MARKER_TEXT, "--provenance", "talaria setup"]]
        for args in steps:
            r = oneoff(ctx, image, data, args)
            if r.returncode != 0:
                raise ValueError(f"gbrain {args[0]} failed: {last_line(r)}")
        try:
            v = parse_schema(oneoff(ctx, image, data, ["doctor", "--json"]).stdout)
        except ValueError as e:
            raise ValueError(f"gbrain doctor failed after init: {e}") from None
        write_schema(data, v)
        (data / INIT_FILE).write_text("1\n")
        return [f"gbrain brain initialized in {data} (PGLite, schema {v})"]

    @staticmethod
    def data_version(data_dir):
        f = Path(data_dir) / SCHEMA_FILE
        if f.is_symlink() or not f.is_file():
            return None
        try:
            return int(f.read_text().strip())
        except (OSError, ValueError, UnicodeDecodeError):
            return None

    def rehearse(self, ctx, st, image, copy, stage) -> dict:
        """Offline, without the env file: doctor (applies the migrations, reports the
        schema) and a recall of the marker page, both on the copy."""
        from talaria.rehearse import Permanent
        tag = image["tag"]
        before = self.data_version(copy)
        try:
            doc = oneoff(ctx, image["id"], copy, ["doctor", "--json"])
            try:
                after = parse_schema(doc.stdout)
            except ValueError as e:
                raise Permanent(f"gbrain doctor on the copy is not ok: {e}",
                                [("gbrain doctor (last lines)", tail(doc))]) from None
            rec = oneoff(ctx, image["id"], copy, ["recall", "--query", MARKER])
        except subprocess.TimeoutExpired as e:
            raise Permanent(f"{tag} did not finish its checks on the copy within "
                            f"{e.timeout:.0f} s") from None
        if rec.returncode != 0 or MARKER not in rec.stdout.lower():
            raise Permanent(f"{tag} could not recall Talaria's marker page on the copy",
                            [("gbrain recall (last lines)", tail(rec))])
        return {"tag": tag, "digest": image.get("digest"), "before": before, "after": after}

    def report_lines(self, ctx, report: dict) -> tuple[list[str], list]:
        before = report["before"]
        return [f"Brain schema: {before if before is not None else 'unknown'} → "
                f"{report['after']}"], []

    def pending_extra(self, report: dict) -> dict:
        return {"schema_after": report["after"]}

    def before_start(self, ctx, pending: dict) -> tuple[str | None, list]:
        """The new release opens (and migrates) the stopped production brain once, offline,
        so its schema can be compared with the rehearsal's; the CLI cannot open the brain
        while the server holds it (spike §8)."""
        r = oneoff(ctx, pending["image"]["id"], ctx.conf.data_dir, ["doctor", "--json"])
        try:
            v = parse_schema(r.stdout)
        except ValueError as e:
            return f"gbrain doctor is not ok: {e}", [("gbrain doctor (last lines)", tail(r))]
        write_schema(ctx.conf.data_dir, v)
        want = pending.get("schema_after")
        if want is not None and v != want:
            return f"the brain schema is {v}, the rehearsal expected {want}", []
        return None, []

    def maintenance(self, ctx) -> str | None:
        """`gbrain dream`: one maintenance cycle, with network and the provider keys."""
        r = oneoff(ctx, f"{self.local_image}:current", ctx.conf.data_dir, ["dream"],
                   network=True, env=True, timeout=3600)
        return None if r.returncode == 0 else _redact(ctx, last_line(r))

    def ready_text(self, ctx) -> str:
        return (f"gbrain is running at http://{ctx.conf.bind_ip}:{ctx.conf.dashboard_port} "
                f"(admin UI /admin; its token is {TOKEN_KEY} in {ctx.paths.app_env}). "
                f"MCP connectors use {ctx.conf.dashboard_public_url}/mcp once you publish it "
                "(README: gbrain)")

    def initial_conf(self, ctx) -> str:
        return ("# Talaria settings; see README.\napp = gbrain\n"
                f"data_dir = {self.default_data_dir}\n"
                "# required: the https address MCP connectors use, e.g.\n"
                "# dashboard.public_url = https://<host>.<tailnet>.ts.net:8443\n")


APP = Gbrain()

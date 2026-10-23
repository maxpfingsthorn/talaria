from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets as _secrets
import shutil
import sqlite3
import subprocess
import sys
from string import Template

from talaria import service
from talaria.apps.base import App, LoginRefused
from talaria.conf import parse_kv, write_env_value
from talaria.ctx import TooLarge
from talaria.images import RevisionMismatch
from talaria.shell import CommandError
from talaria.state import ensure_dir
from talaria.upstream import _ls_remote

TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
BASE = ("gcr.io/distroless/static-debian12@sha256:"
        "afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab")
ARCH = {"x86_64": "amd64", "aarch64": "arm64"}
MAX_BINARY = 300 * 1024 * 1024
NAME = "talaria-rehearse"
ENV = ["CONFIG_FILE=/data/config.yaml", "SERVER_HOST=0.0.0.0", "DATABASE_DRIVER=sqlite",
       "SQLITE_PATH=/data/clawvisor.db", "VAULT_KEY_FILE=/data/vault.key",
       "CLAWVISOR_RELAY_KEY_FILE=/data/daemon-ed25519.key",
       "CLAWVISOR_RELAY_E2E_KEY_FILE=/data/daemon-x25519.key",
       "CLAWVISOR_DAEMON_DATA_DIR=/data", "CLAWVISOR_CONTAINER=1", "MAX_USERS=1",
       "CLAWVISOR_AUTO_UPDATE_ENABLED=false", "HOME=/data"]


def migrations(db) -> list[str]:
    """Names of migrations already applied, oldest first. Read-only; never writes to db."""
    if not db.is_file() or db.is_symlink():
        return []
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)  # pragma: no mutate
    try:
        return [r[0] for r in c.execute("select name from schema_migrations order by name")]  # pragma: no mutate
    except sqlite3.Error:
        return []
    finally:
        c.close()


def _migrations_or_raise(db) -> list[str]:
    """Like migrations(), but lets a sqlite3.Error through instead of treating it as an
    empty list: after_start needs to tell "couldn't read the table" apart from "genuinely
    no migrations yet" (the latter is normal before the very first start)."""
    if not db.is_file() or db.is_symlink():
        return []
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)  # pragma: no mutate
    try:
        return [r[0] for r in c.execute("select name from schema_migrations order by name")]  # pragma: no mutate
    finally:
        c.close()


def _redact(ctx, copy, text: str) -> str:
    """Replace secret values with '***' before they can reach Permanent.details, which
    check.py forwards unchanged to Telegram: the real app_env (mounted into the rehearsal
    via --env-file, so it carries JWT_SECRET and friends) and the copy's vault.key, in
    case the app ever echoes its environment or a decrypted secret on a failed boot.
    Values shorter than 4 chars are skipped (too short to usefully redact, and would
    shred ordinary log text) except JWT_SECRET and the vault key, which are always
    redacted regardless of length."""
    values = []
    try:
        env = parse_kv(ctx.paths.app_env.read_text())
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        env = {}
    values += [v for k, v in env.items() if v and (k == "JWT_SECRET" or len(v) >= 4)]  # pragma: no mutate (values is always [] here; += and = are the same)
    vault = copy / "vault.key"
    if vault.is_file() and not vault.is_symlink():
        try:
            v = vault.read_text().strip()
        except (OSError, UnicodeDecodeError):
            v = ""  # pragma: no mutate (only its truthiness is tested below; None behaves the same)
        if v:
            values.append(v)
    for v in sorted(values, key=len, reverse=True):   # longest first: avoid partial overlaps
        text = text.replace(v, "***")
    return text


def _get(ctx, url: str, dest, max_bytes: int, tag: str, label: str) -> int:
    """ctx.download, with an oversized asset treated as a bad publish (Permanent) rather
    than a transient network error: a release's assets are immutable once published, so
    retrying tomorrow would just re-download the same oversized file and crash again."""
    try:
        return ctx.download(url, dest, max_bytes)
    except TooLarge as e:
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
    backup_exclude = (".login-link", ".clawvisor/.local-session")  # throwaway login HOMEs / CLI session
    can_adopt = False

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
        return set(tags)

    def image_refs(self, ctx) -> list[str]:
        return [self.local_image]

    def fetch(self, ctx, tag: str, commit: str) -> dict:
        from talaria.rehearse import Transient
        machine = ctx.sh.run(["uname", "-m"]).stdout.strip()
        try:
            arch = ARCH[machine]
        except KeyError:
            raise RevisionMismatch(f"unsupported architecture: {machine}") from None
        asset = f"clawvisor-server-linux-{arch}"
        base = f"{ctx.conf.repo}/releases/download/{tag}"
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
            want = {ln.split()[-1]: ln.split()[0] for ln in
                    (work / "checksums.txt").read_text().splitlines() if len(ln.split()) == 2}
            got = hashlib.sha256((work / "clawvisor-server").read_bytes()).hexdigest()
            if want.get(asset) != got:
                raise RevisionMismatch(f"{tag}: checksum of {asset} does not match checksums.txt")
            (work / "Containerfile").write_text(Template(
                (ctx.paths.templates_dir / "clawvisor.Containerfile").read_text()
            ).substitute(base=BASE))
            iid = ctx.sh.run(["podman", "build", "-q", "--pull=missing", "--timestamp", "0",
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

    def rehearse(self, ctx, st, image, copy, stage) -> dict:
        """Start the new image offline on the copy of the data dir, wait for it to become
        ready, then report how many migrations it applied. --network=none keeps the
        rehearsal from touching anything outside the copy."""
        from talaria.rehearse import Permanent, Transient
        before = migrations(copy / "clawvisor.db")
        ctx.sh.run(["podman", "rm", "-f", NAME], check=False, timeout=120)
        envs = [a for e in ENV for a in ("-e", e)]
        try:
            try:
                ctx.sh.run(["podman", "run", "-d", "--name", NAME, "--network=none",
                            "--userns=keep-id:uid=65532,gid=65532", "-v", f"{copy}:/data:Z",
                            "--env-file", str(ctx.paths.app_env), *envs, image["id"]],
                           timeout=300)
            except (CommandError, subprocess.TimeoutExpired) as e:
                raise Transient(_redact(
                    ctx, copy, f"could not start {image['tag']} for the rehearsal: {e}")) from None
            ready, waited = False, 0
            while waited < max(ctx.conf.settle_seconds, 120):
                if ctx.sh.run(["podman", "exec", NAME, "/clawvisor-server", "healthcheck"],
                              check=False, timeout=30).returncode == 0:
                    ready = True
                    break
                ctx.sleep(2)
                waited += 2
            if not ready:
                tail = ctx.sh.run(["podman", "logs", "--tail", "20", NAME], check=False,
                                  timeout=60)
                log = _redact(ctx, copy, (tail.stdout + tail.stderr).strip())
                raise Permanent(f"{image['tag']} did not become ready on the copy",
                                [("Log (last lines)", log)])
        finally:
            ctx.sh.run(["podman", "stop", "-t", "30", NAME], check=False, timeout=120)
            ctx.sh.run(["podman", "rm", "-f", NAME], check=False, timeout=120)
        after = migrations(copy / "clawvisor.db")
        return {"tag": image["tag"], "digest": image.get("digest"), "before": len(before),
                "after": len(after), "new": [n for n in after if n not in set(before)],
                "latest": after[-1] if after else None}

    def report_lines(self, ctx, report: dict) -> tuple[list[str], list]:
        lines = [f"Database migrations: {report['before']} → {report['after']}"]
        blocks = [(f"Migrations that will run ({len(report['new'])})", "\n".join(report["new"]))] \
            if report["new"] else []
        return lines, blocks

    def pending_extra(self, report: dict) -> dict:
        return {"migrations_after": report["after"]}

    def health(self, ctx) -> str | None:
        code, body = ctx.http_get(f"http://{ctx.conf.bind_ip}:{ctx.conf.dashboard_port}/ready", 5.0)
        if code != 200:
            return f"/ready answered {code or 'nothing'}"
        try:
            r = json.loads(body)
        except ValueError:
            return "/ready did not answer JSON"
        if not isinstance(r, dict):
            return "/ready did not answer a JSON object"
        bad = [f"{k} {r.get(k)}" for k in ("status", "db", "vault") if r.get(k) != "ok"]
        return f"/ready reports {', '.join(bad)}" if bad else None

    def after_start(self, ctx, pending: dict) -> str | None:
        try:
            n = len(_migrations_or_raise(ctx.conf.data_dir / "clawvisor.db"))
        except sqlite3.Error as e:
            return f"could not read migrations: {e}"
        want = pending.get("migrations_after")
        if want is not None and n != want:
            return f"the database has {n} migrations, the rehearsal expected {want}"
        return None

    @staticmethod
    def data_version(data_dir):
        names = migrations(data_dir / "clawvisor.db")
        return names[-1] if names else None

    def prepare(self, ctx) -> list[str]:
        out = []
        data = ctx.conf.data_dir
        data.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(data, 0o700)
        env = parse_kv(ctx.paths.app_env.read_text()) if ctx.paths.app_env.exists() else {}
        if "JWT_SECRET" not in env:
            write_env_value(ctx.paths.app_env, "JWT_SECRET", _secrets.token_hex(32))
            out.append(f"JWT secret generated in {ctx.paths.app_env}")
        key = data / "vault.key"
        if not key.exists():
            if (data / "clawvisor.db").exists():
                raise ValueError(f"{key} is missing but a database exists; restore the key "
                                 "from a backup instead of generating a new one")
            fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(base64.b64encode(_secrets.token_bytes(32)).decode() + "\n")
            except BaseException:
                # an interrupted write (e.g. disk full) must never leave an empty key behind
                # for the next prepare() to mistake for a real one
                key.unlink(missing_ok=True)
                raise
            out.append(f"vault key generated in {key}")
        elif not key.read_bytes().strip():
            raise ValueError(f"{key} exists but is empty; a previous write may have failed. "
                             "Restore it from a backup, or remove it to generate a new one.")
        return out

    def ready_text(self, ctx) -> str:
        return (f"Clawvisor is running at http://{ctx.conf.bind_ip}:{ctx.conf.dashboard_port}. "
                f"First login: run `{ctx.paths.bin_link} login-link` in your own terminal")

    def initial_conf(self, ctx) -> str:
        return (f"# Talaria settings; see README.\napp = clawvisor\n"
                f"data_dir = {self.default_data_dir}\n")


    has_login_link = True

    def login_link(self, ctx, oauth_request=None) -> list[str]:
        if oauth_request:
            raise LoginRefused("only gbrain takes an OAuth request")
        if not service.is_active(ctx):
            raise LoginRefused("Clawvisor is not running")
        os.chdir("/")  # podman fails if the service user cannot enter the caller's cwd
        # The server writes ~/.clawvisor/.local-session with server_url = PUBLIC_URL when that is
        # set, and the dashboard command posts to it; the endpoint only answers 127.0.0.1. So
        # write our own session file (no token: the server issues the link) in a throwaway HOME.
        tmp = ctx.conf.data_dir / ".login-link"
        fds = []
        try:
            # the data dir is agent-writable: nothing below follows a planted link
            if os.path.lexists(tmp):
                if tmp.is_symlink() or not tmp.is_dir():
                    os.unlink(tmp)
                else:
                    shutil.rmtree(tmp)
            try:
                os.mkdir(tmp, 0o700)
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                fds.append(os.open(tmp, flags))
                os.mkdir(".clawvisor", 0o700, dir_fd=fds[0])
                fds.append(os.open(".clawvisor", flags, dir_fd=fds[0]))
                fd = os.open(".local-session", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=fds[1])
            except OSError:
                raise LoginRefused("could not prepare the login session") from None
            with os.fdopen(fd, "w") as f:
                json.dump({"server_url": f"http://localhost:{ctx.app.container_port}",
                           "magic_token": ""}, f)
            r = ctx.sh.run(["podman", "exec", "-e", "HOME=/data/.login-link", ctx.app.container,
                            "/clawvisor-server", "dashboard", "--no-open"], check=False, timeout=30)
        except subprocess.TimeoutExpired:
            raise LoginRefused("podman exec timed out") from None
        finally:
            for d in fds:
                os.close(d)
            shutil.rmtree(tmp, ignore_errors=True)
        if r.returncode != 0:   # its output may hold the link: log only the exit code
            print(f"[talaria] clawvisor login-link: exit {r.returncode}", file=sys.stderr)
            raise LoginRefused("could not get a login link from Clawvisor")
        # Clawvisor prints its own container-internal port (25297); the Quadlet publishes
        # that on ctx.conf.dashboard_port, which can differ -- rewrite both host and port.
        # One line per published address; the token appears only in these lines.
        port = ctx.conf.dashboard_port
        lines = []
        public = ctx.conf.dashboard_public_url
        if public:
            lines.append(re.sub(r"http://localhost:\d+", lambda _: public, r.stdout.strip()))
        for ip in ctx.conf.bind_ips:
            lines.append(re.sub(r"http://localhost:\d+", f"http://{ip}:{port}", r.stdout.strip()))
        ts = ctx.conf.tailscale_ip if "tailscale" in ctx.conf.dashboard_bind.split() else None
        if any(ip != ts for ip in ctx.conf.bind_ips):
            lines.append(f"Through an SSH tunnel, open it as http://127.0.0.1:{port}/... instead "
                         "(rest of the link unchanged).")
        return lines


APP = Clawvisor()

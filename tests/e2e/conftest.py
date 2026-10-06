"""e2e fixtures. Needs: Linux, systemd, rootless podman, passwordless sudo (GitHub runner).
Enabled only with TALARIA_E2E=1. Spike outcome (Task 22 Step 1): rootless podman with keep-id:uid=10000 and
systemd-run work on ubuntu-24.04 runners once the AppArmor userns sysctl is off; restarting
user@<uid>.service times out, so test_08 simulates the boot instead."""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from tests.e2e.fake_telegram import make_server

ROOT = Path(__file__).resolve().parents[2]
WORK = Path("/tmp/talaria-e2e")
USER = "hermes"

pytestmark = pytest.mark.e2e


def sh(*argv, check=True, **kw):
    return subprocess.run([str(a) for a in argv], check=check, text=True,
                          capture_output=True, **kw)


def uid(user=USER):
    return int(sh("id", "-u", user).stdout)


def as_user(*argv, user=USER, env=(), check=True, cwd="/", **kw):
    u = uid(user)
    # env -i: the runner's XDG_* and other variables survive sudo on GitHub runners
    return sh("sudo", "-u", user, "-H", "env", "-i", f"HOME=/home/{user}", f"USER={user}",
              f"LOGNAME={user}", "PATH=/usr/local/bin:/usr/bin:/bin",
              f"XDG_RUNTIME_DIR=/run/user/{u}",
              f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{u}/bus", *env, *argv,
              check=check, cwd=cwd, **kw)


def bus_ready(user=USER) -> bool:
    return sh("sudo", "test", "-S", f"/run/user/{uid(user)}/bus", check=False).returncode == 0


def talaria(*argv, user=USER, **kw):
    return as_user(f"/home/{user}/.local/bin/talaria", *argv, user=user, **kw)


class Upstream:
    """Local git repo + registry with dummy Hermes releases v2026.1.N."""

    def __init__(self):
        self.git = WORK / "hermes.git"
        self.clone = WORK / "hermes-work"

    def publish(self, n: int, mode: str = "ok") -> str:
        tag = f"v2026.1.{n}"
        sh("git", "-C", self.clone, "commit", "-q", "--allow-empty", "-m", tag)
        sh("git", "-C", self.clone, "tag", tag)
        sh("git", "-C", self.clone, "push", "-q", "origin", "HEAD", tag)
        rev = sh("git", "-C", self.clone, "rev-parse", "HEAD").stdout.strip()
        ref = f"localhost:5000/hermes-agent:{tag}"
        sh("podman", "build", "-q", "-t", ref, "--build-arg", f"LATEST={n}",
           "--build-arg", f"MODE={mode}", "--build-arg", f"REVISION={rev}",
           ROOT / "tests/e2e/dummy", timeout=900)
        sh("podman", "push", "-q", "--tls-verify=false", ref, timeout=600)
        return tag


@pytest.fixture(scope="session")
def _base_env():
    """Pieces every app's e2e tests share: the WORK dir, a world-readable copy of this
    checkout the service user can clone, and the fake Telegram server."""
    if os.environ.get("TALARIA_E2E") != "1":
        pytest.skip("set TALARIA_E2E=1 on a disposable machine")
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(mode=0o755)
    src = WORK / "talaria-src"
    shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(".venv", "mutants"))
    sh("chmod", "-R", "a+rX", WORK)
    srv, tg = make_server()
    yield {"src": src, "tg": tg}
    srv.shutdown()


@pytest.fixture(scope="session")
def env(_base_env):
    sh("podman", "run", "-d", "--name", "e2e-registry", "-p", "5000:5000",
       "docker.io/library/registry:2", check=False)
    sh("git", "init", "-q", "--bare", WORK / "hermes.git")
    sh("git", "clone", "-q", WORK / "hermes.git", WORK / "hermes-work")
    sh("git", "-C", WORK / "hermes-work", "config", "user.email", "e2e@example.invalid")
    sh("git", "-C", WORK / "hermes-work", "config", "user.name", "e2e")
    up = Upstream()
    up.publish(1)
    sh("chmod", "-R", "a+rX", WORK)
    return {**_base_env, "up": up}


CONF = """image = localhost:5000/hermes-agent
hermes_repo = /tmp/talaria-e2e/hermes.git
talaria_repo = /tmp/talaria-e2e/talaria-src
registry_tls_verify = false
min_release = v2026.1.1
settle_seconds = 10
telegram_api = http://127.0.0.1:8081
disk.floor_gb = 0.2
"""


def seed_conf(user=USER):
    as_user("mkdir", "-p", f"/home/{user}/.config/talaria", user=user)
    as_user("sh", "-c", f"cat > /home/{user}/.config/talaria/talaria.conf", user=user,
            input=CONF)
    as_user("git", "config", "--global", "--add", "safe.directory", "*", user=user)


def root_block(out: str) -> str:
    m = re.search(r"ACTION REQUIRED: run this block as root.*?\n(.*?)(?:\n\S+:|\Z)", out, re.S)
    assert m, out
    return m.group(1)


def wait_for(fn, timeout=120, step=1.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return
        time.sleep(step)
    raise AssertionError("condition not met in time")


PAIR_CODE_RE = re.compile(r"/pair ([A-Z0-9]{8})")


class AppEnv:
    """A service user Talaria is being set up for in --dev mode, with its own fake
    Telegram chat. The account already exists and its bus is up by the time a test
    gets this (see the fixture that builds it)."""

    def __init__(self, user: str, app: str, src: Path, telegram):
        self.user, self.app, self.src, self.telegram = user, app, src, telegram

    def _conf_file(self) -> str:
        return f"/home/{self.user}/.config/talaria/talaria.conf"

    def conf(self, text: str) -> None:
        as_user("mkdir", "-p", f"/home/{self.user}/.config/talaria", user=self.user)
        as_user("sh", "-c", f"cat > {self._conf_file()}", user=self.user, input=text)

    def conf_add(self, text: str) -> None:
        as_user("sh", "-c", f"cat >> {self._conf_file()}", user=self.user, input=text)

    def setup_until_done(self, *extra, timeout=900) -> str:
        """Run `talaria setup --dev` for this user, handling whatever it asks for next
        (a root block, set-token, or a /pair code) and re-running, until it exits 0 with
        output ending in DONE. Returns the concatenated output of every run."""
        out = ""
        argv = [str(self.src / "bin/talaria"), "setup", "--dev", "--app", self.app,
                "--user", self.user, *extra]
        while True:
            p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True)
            chunk = ""
            for line in p.stdout:
                chunk += line
                m = PAIR_CODE_RE.search(line)
                if m:
                    self.telegram.inject(f"/pair {m[1]}")
            rc = p.wait(timeout=timeout)
            out += chunk
            if rc == 0:
                assert chunk.rstrip().endswith("DONE"), out
                return out
            if rc == 10 and "ACTION REQUIRED: run this block as root" in chunk:
                sh("sudo", "bash", "-euc", root_block(chunk))
                wait_for(lambda: bus_ready(self.user))
                continue
            if rc == 10 and "set-token" in chunk:
                as_user(f"/home/{self.user}/.local/bin/talaria", "set-token", user=self.user,
                        input="123456:" + "a" * 35 + "\n")
                continue
            raise AssertionError(f"setup did not finish: {out}")


@pytest.fixture(scope="session")
def cv_env(_base_env):
    """A second service user, cvtest, running Clawvisor; built directly from the real
    upstream releases (no registry or dummy image, unlike Hermes's `env`)."""
    user = "cvtest"
    r = sh(_base_env["src"] / "bin/talaria", "setup", "--dev", "--app", "clawvisor",
          "--user", user, check=False)
    assert r.returncode == 10, r.stdout
    sh("sudo", "bash", "-euc", root_block(r.stdout))
    wait_for(lambda: bus_ready(user))
    as_user("git", "config", "--global", "--add", "safe.directory", "*", user=user)
    return AppEnv(user=user, app="clawvisor", src=_base_env["src"], telegram=_base_env["tg"])

"""e2e fixtures. Needs: Linux, systemd, rootless podman, passwordless sudo (GitHub runner).
Enabled only with TALARIA_E2E=1. Spike outcome (Task 22 Step 1): <record here>."""
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


def as_user(*argv, user=USER, env=(), check=True, **kw):
    u = uid(user)
    # env -i: the runner's XDG_* and other variables survive sudo on GitHub runners
    return sh("sudo", "-u", user, "-H", "env", "-i", f"HOME=/home/{user}", f"USER={user}",
              f"LOGNAME={user}", "PATH=/usr/local/bin:/usr/bin:/bin",
              f"XDG_RUNTIME_DIR=/run/user/{u}",
              f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{u}/bus", *env, *argv,
              check=check, cwd="/", **kw)


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
def env():
    if os.environ.get("TALARIA_E2E") != "1":
        pytest.skip("set TALARIA_E2E=1 on a disposable machine")
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(mode=0o755)
    src = WORK / "talaria-src"          # world-readable copy the service user can clone
    shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(".venv", "mutants"))
    sh("chmod", "-R", "a+rX", WORK)
    sh("podman", "run", "-d", "--name", "e2e-registry", "-p", "5000:5000",
       "docker.io/library/registry:2", check=False)
    sh("git", "init", "-q", "--bare", WORK / "hermes.git")
    sh("git", "clone", "-q", WORK / "hermes.git", WORK / "hermes-work")
    sh("git", "-C", WORK / "hermes-work", "config", "user.email", "e2e@example.invalid")
    sh("git", "-C", WORK / "hermes-work", "config", "user.name", "e2e")
    up = Upstream()
    up.publish(1)
    sh("chmod", "-R", "a+rX", WORK)
    srv, tg = make_server()
    yield {"src": src, "up": up, "tg": tg}
    srv.shutdown()


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

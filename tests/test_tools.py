import json
import pytest

# --- tools/tailscale-admin-redirect.py ---------------------------------

import copy
import glob
import os
import subprocess
import sys
from pathlib import Path

REDIRECT = Path(__file__).resolve().parent.parent / "tools" / "tailscale-admin-redirect.py"
HOST = "h.example.ts.net"
FAKE_TS = '''#!/usr/bin/env python3
import os, sys
d = os.environ["FAKE_TS_DIR"]
a = sys.argv[1:]
if a == ["version"]:
    print(open(d + "/version").read().strip()); print("  go1.x")
elif a == ["serve", "status", "--json"]:
    sys.stdout.write(open(d + "/status.json").read())
elif a[:4] == ["debug", "localapi", "POST", "/localapi/v0/serve-config"] and a[4:] == ["-"]:
    data = sys.stdin.read()
    open(d + "/posted.json", "w").write(data)
    if os.path.exists(d + "/failapply"):
        open(d + "/status.json", "w").write(data)
        sys.stderr.write("timed out"); sys.exit(3)
    if os.path.exists(d + "/fail"):
        sys.stderr.write("denied"); sys.exit(3)
    open(d + "/status.json", "w").write(data)
else:
    sys.exit(9)
'''


def ts_config():
    h = {"/mcp": {"Proxy": "http://127.0.0.1:3131/mcp"}, "/token": {"Proxy": "http://127.0.0.1:3131/token"}}
    return {
        "TCP": {"443": {"HTTPS": True}, "10000": {"HTTPS": True}},
        "Web": {
            f"{HOST}:443": {"Handlers": h},
            f"{HOST}:10000": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3131"}}},
        },
        "AllowFunnel": {f"{HOST}:443": True},
    }


@pytest.fixture
def ts(tmp_path):
    (tmp_path / "bin").mkdir()
    fake = tmp_path / "bin" / "tailscale"
    fake.write_text(FAKE_TS)
    fake.chmod(0o755)
    (tmp_path / "version").write_text("1.102.4")
    cfg = ts_config()
    (tmp_path / "status.json").write_text(json.dumps(cfg))

    def run(*args):
        env = dict(os.environ, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}", FAKE_TS_DIR=str(tmp_path))
        return subprocess.run([sys.executable, str(REDIRECT), *args], cwd=tmp_path, env=env,
                              capture_output=True, text=True, timeout=30)

    run.dir = tmp_path
    run.orig = cfg
    run.status = lambda: json.loads((tmp_path / "status.json").read_text())
    run.backups = lambda: glob.glob(str(tmp_path / "tailscale-serve.before-admin-redirect.*.json"))
    return run


def test_adds_exactly_one_handler(ts):
    r = ts("--host", HOST)
    assert r.returncode == 0, r.stderr
    want = copy.deepcopy(ts.orig)
    want["Web"][f"{HOST}:443"]["Handlers"]["/admin"] = {
        "Redirect": "302:https://h.example.ts.net:10000${REQUEST_URI}"}
    assert ts.status() == want
    (b,) = ts.backups()
    assert json.load(open(b)) == ts.orig
    assert oct(os.stat(b).st_mode & 0o777) == "0o600"
    assert b in r.stdout


def test_dry_run_changes_nothing(ts):
    r = ts("--host", HOST, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert ts.status() == ts.orig
    assert not (ts.dir / "posted.json").exists()


def test_refuses_when_admin_exists(ts):
    cfg = ts.orig
    cfg["Web"][f"{HOST}:443"]["Handlers"]["/admin"] = {"Proxy": "http://x"}
    (ts.dir / "status.json").write_text(json.dumps(cfg))
    r = ts("--host", HOST)
    assert r.returncode == 1 and "/admin is already configured on 443" in r.stderr
    assert not (ts.dir / "posted.json").exists()


def test_refuses_without_443_handlers(ts):
    r = ts("--host", "other.example.ts.net")
    assert r.returncode == 1 and "serves no paths" in r.stderr
    assert not (ts.dir / "posted.json").exists()


def test_refuses_old_tailscale(ts):
    (ts.dir / "version").write_text("1.90.0")
    r = ts("--host", HOST)
    assert r.returncode == 1 and "needs Tailscale 1.92 or newer" in r.stderr
    assert not (ts.dir / "posted.json").exists()


def test_post_failure_reports_stderr(ts):
    (ts.dir / "fail").write_text("")
    r = ts("--host", HOST)
    assert r.returncode == 1 and "denied" in r.stderr
    assert ts.backups() == [] and "removed the backup" in r.stderr


def test_undo_restores(ts):
    assert ts("--host", HOST).returncode == 0
    assert ts.status() != ts.orig
    (b,) = ts.backups()
    r = ts("--undo", b)
    assert r.returncode == 0, r.stderr
    assert "restored" in r.stdout
    assert ts.status() == ts.orig


def test_undo_rejects_non_object(ts):
    p = ts.dir / "bad.json"
    p.write_text("[1]")
    r = ts("--undo", str(p))
    assert r.returncode == 1 and not (ts.dir / "posted.json").exists()


@pytest.mark.parametrize("args", [("--host", "x;rm"), ("--host", HOST, "--admin-port", "443"),
                                  ("--host", HOST, "--admin-port", "0"), ("--host", HOST, "--admin-port", "70000")])
def test_bad_host_or_port(ts, args):
    r = ts(*args)
    assert r.returncode != 0 and not (ts.dir / "posted.json").exists()


def test_dry_run_writes_no_backup_and_does_not_block_real_run(ts):
    assert ts("--host", HOST, "--dry-run").returncode == 0
    assert ts.backups() == []
    assert ts("--host", HOST).returncode == 0
    assert len(ts.backups()) == 1


def test_backup_stamp_has_seconds(ts):
    import re
    assert ts("--host", HOST).returncode == 0
    (b,) = ts.backups()
    assert re.search(r"\.\d{14}\.json$", b)


@pytest.mark.parametrize("body", ['{"foo": {}}', '{"Web": 5}', '{"TCP": {}, "X": [1]}', '"x"'])
def test_undo_rejects_non_serve_configs(ts, body):
    p = ts.dir / "bad.json"
    p.write_text(body)
    r = ts("--undo", str(p))
    assert r.returncode == 1 and "does not look like" in r.stderr
    assert not (ts.dir / "posted.json").exists()


def test_undo_shows_current_and_restored(ts):
    assert ts("--host", HOST).returncode == 0
    (b,) = ts.backups()
    r = ts("--undo", b)
    assert r.returncode == 0
    assert f"current  {HOST}:443: /admin, /mcp, /token" in r.stdout
    assert f"restored {HOST}:443: /mcp, /token" in r.stdout


def test_undo_aborts_when_config_changes(ts):
    p = ts.dir / "old.json"
    p.write_text(json.dumps(ts_config()))
    _race(ts)
    r = ts("--undo", str(p))
    assert r.returncode == 1 and "changed while this ran" in r.stderr
    assert not (ts.dir / "posted.json").exists()


def test_add_aborts_when_config_changes(ts):
    _race(ts)
    r = ts("--host", HOST)
    assert r.returncode == 1 and "changed while this ran" in r.stderr
    assert not (ts.dir / "posted.json").exists()
    assert len(ts.backups()) == 1 and "kept the backup" in r.stderr
    assert "config may have changed" in r.stderr


def test_failed_post_that_was_applied_keeps_the_backup(ts):
    (ts.dir / "failapply").write_text("")
    r = ts("--host", HOST)
    assert r.returncode == 1 and "timed out" in r.stderr
    (b,) = ts.backups()
    assert json.load(open(b)) == ts.orig
    assert "kept the backup" in r.stderr and "config may have changed" in r.stderr
    assert "nothing was applied" not in r.stderr


def test_success_keeps_backup_and_hint_is_exact(ts):
    r = ts("--host", HOST)
    assert r.returncode == 0
    (b,) = ts.backups()
    assert f"done; undo with: python3 -I {REDIRECT} --undo {b}" in r.stdout


def _race(ts):
    """Make the fake tailscale return a different config on every second status call."""
    bin_ = ts.dir / "bin" / "tailscale"
    bin_.write_text(bin_.read_text().replace(
        'elif a == ["serve", "status", "--json"]:\n    sys.stdout.write(open(d + "/status.json").read())',
        'elif a == ["serve", "status", "--json"]:\n'
        '    n = int(open(d + "/n").read()) if os.path.exists(d + "/n") else 0\n'
        '    open(d + "/n", "w").write(str(n + 1))\n'
        '    c = json.load(open(d + "/status.json"))\n'
        '    if n >= 1: c["AllowFunnel"] = {}\n'
        '    sys.stdout.write(json.dumps(c))').replace("import os, sys", "import json, os, sys"))

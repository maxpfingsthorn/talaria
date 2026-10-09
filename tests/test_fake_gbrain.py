# tests/test_fake_gbrain.py
"""The e2e fake gbrain (tests/e2e/fake_gbrain/fake_gbrain.c) speaks the CLI the adapter
uses. Built natively here (no -static); skipped without gcc."""
import json
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from talaria.apps.gbrain import MARKER, MARKER_TEXT, parse_schema

DEGRADED = "note: search degraded (keyword_only_no_embedding_provider)\n"
SRC = Path(__file__).parent / "e2e/fake_gbrain/fake_gbrain.c"


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    if shutil.which("gcc") is None:
        pytest.skip("gcc not installed")
    out = tmp_path_factory.mktemp("fake")
    bins = {}
    for version, schema in (("0.60.1.0", 1), ("0.60.2.0", 2)):
        b = out / f"gbrain-{schema}"
        subprocess.run(["gcc", "-O2", f'-DVERSION="{version}"', f"-DSCHEMA={schema}", "-o",
                        str(b), str(SRC)], check=True, timeout=120)
        bins[schema] = b
    return bins


def gb(binary, home, *args, env=()):
    return subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=30,
                          env={"PATH": "/usr/bin:/bin", "GBRAIN_HOME": str(home), **dict(env)})


def test_cli(build, tmp_path):
    old, new = build[1], build[2]
    assert gb(old, tmp_path, "--version").stdout == "gbrain 0.60.1.0\n"
    assert gb(old, tmp_path, "doctor", "--json").returncode == 1        # no brain yet
    assert gb(old, tmp_path, "init", "--pglite").returncode == 0
    assert gb(old, tmp_path, "config", "set", "self_upgrade.mode", "off").returncode == 0
    assert gb(old, tmp_path, "remember", MARKER_TEXT).returncode == 1       # needs provenance
    assert gb(old, tmp_path, "remember", MARKER_TEXT, "--provenance", "test").returncode == 0
    assert parse_schema(gb(old, tmp_path, "doctor", "--json").stdout) == 1
    assert parse_schema(gb(new, tmp_path, "doctor", "--json").stdout) == 2     # migrates
    with pytest.raises(ValueError, match="AHEAD"):
        parse_schema(gb(old, tmp_path, "doctor", "--json").stdout)
    assert MARKER in gb(old, tmp_path, "recall", "--query", MARKER).stdout
    assert gb(old, tmp_path, "recall", "--query", "nothing like it").stdout == DEGRADED
    doc = gb(old, tmp_path, "doctor", "--json").stdout
    assert doc.startswith("[backup]") and "[AGENT]" in doc and doc.endswith("create one\n")
    assert gb(old, tmp_path, "dream").returncode == 0
    r = gb(old, tmp_path, "dream", env={"FAKE_DREAM_FAIL": "1"})
    assert r.returncode == 1 and r.stderr == "dream: phase synthesize failed\n"
    assert (tmp_path / ".gbrain/dreams").read_text() == "dream\n"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_serve_answers_health_and_stops_on_sigterm(build, tmp_path):
    gb(build[1], tmp_path, "init", "--pglite")
    port = free_port()
    p = subprocess.Popen([str(build[1]), "serve", "--http", "--bind", "0.0.0.0", "--port",
                          str(port)], env={"GBRAIN_HOME": str(tmp_path)},
                         stdout=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                body = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1).read()
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("the fake server did not answer")
        assert json.loads(body) == {"status": "ok", "version": "0.60.1.0", "engine": "pglite"}
    finally:
        p.terminate()
        assert p.wait(timeout=10) == 0

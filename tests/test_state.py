import json
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from talaria import lock, marker, state
from talaria.ctx import Paths


def test_load_defaults_when_missing(tmp_path):
    st = state.load(Paths(tmp_path))
    assert st == state.DEFAULT
    st["rejected"].append("x")
    assert state.DEFAULT["rejected"] == []  # deep copy


def test_save_is_atomic_and_private(tmp_path):
    p = Paths(tmp_path)
    st = state.load(p)
    st["current"] = {"tag": "v2026.8.3"}
    state.save(p, st)
    assert json.loads(p.state_file.read_text())["current"]["tag"] == "v2026.8.3"
    assert not list(p.state_dir.glob("*.tmp"))
    assert (p.state_dir.stat().st_mode & 0o777) == 0o700


def test_load_merges_new_default_keys(tmp_path):
    p = Paths(tmp_path)
    p.state_dir.mkdir(parents=True)
    p.state_file.write_text('{"version": 1, "current": {"tag": "v1"}}')
    st = state.load(p)
    assert st["current"] == {"tag": "v1"} and st["rejected"] == []


def test_lock_busy(tmp_path):
    p = Paths(tmp_path)
    with lock.op_lock(p):
        with pytest.raises(lock.Busy):
            with lock.op_lock(p):
                pass
    with lock.op_lock(p):  # released
        pass


def test_lock_not_inherited_by_children(tmp_path):
    p = Paths(tmp_path)
    with lock.op_lock(p):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with lock.op_lock(p):  # child still alive, lock must be free
            pass
    finally:
        child.kill()
        child.wait()


def test_marker_roundtrip(tmp_path):
    p = Paths(tmp_path)
    assert marker.read(p) is None
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    marker.write(p, "deploy", "20260927T000000Z-pre-v2026.8.3", {"id": "sha256:a"}, now)
    m = marker.read(p)
    assert m == {"op": "deploy", "backup": "20260927T000000Z-pre-v2026.8.3",
                 "image": {"id": "sha256:a"}, "written": "2026-09-27T00:00:00+00:00"}
    marker.clear(p)
    marker.clear(p)  # idempotent
    assert marker.read(p) is None


def test_write_json_atomic_exact(tmp_path):
    f = tmp_path / "d" / "x.json"
    state.write_json_atomic(f, {"b": 1, "a": [1, 2]})
    assert f.read_text() == '{\n  "a": [\n    1,\n    2\n  ],\n  "b": 1\n}'
    assert (f.stat().st_mode & 0o777) == 0o600
    assert (f.parent.stat().st_mode & 0o777) == 0o700
    assert [p.name for p in f.parent.iterdir()] == ["x.json"]

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULT = {
    "version": 1, "current": None, "previous": None, "pending": None,
    "rejected": [], "failed": [], "last_deploy": None, "op": None,
    "adopt_backup": None, "check_failures": 0, "transient": None,
    "talaria_notified": None, "history_error": None,
}


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def write_json_atomic(path: Path, data) -> None:
    ensure_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def load(paths) -> dict:
    st = copy.deepcopy(DEFAULT)
    if paths.state_file.exists():
        st.update(json.loads(paths.state_file.read_text()))
    return st


def save(paths, st: dict) -> None:
    write_json_atomic(paths.state_file, st)

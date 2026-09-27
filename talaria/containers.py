from __future__ import annotations

import json
from pathlib import Path

_BASE = ["--rm", "--network=none", "--userns=keep-id:uid=10000,gid=10000",
         "--user", "10000:10000"]


class HelperError(Exception):
    pass


def run_helper(ctx, rec: dict, script: str, data: Path, work: Path, args=()) -> dict:
    stem = script.rsplit(".", 1)[0]
    result = Path(work) / f"{stem}.json"
    result.unlink(missing_ok=True)
    argv = ["podman", "run", *_BASE,
            "-v", f"{data}:/opt/data", "-v", f"{ctx.paths.helpers_dir}:/opt/talaria:ro",
            "-v", f"{work}:/opt/talaria-out",
            "-e", "HERMES_HOME=/opt/data", "-e", "HOME=/opt/data",
            "-e", "PYTHONPATH=/opt/hermes:/opt/talaria",
            "-e", f"TALARIA_RESULT=/opt/talaria-out/{stem}.json",
            "-w", "/opt/hermes", "--entrypoint", "/opt/hermes/.venv/bin/python",
            rec["id"], f"/opt/talaria/{script}", *args]
    r = ctx.sh.run(argv, check=False, timeout=900)
    if not result.exists():
        tail = " | ".join((r.stderr or r.stdout).strip().splitlines()[-5:])
        raise HelperError(f"{script} wrote no result (exit {r.returncode}): {tail}")
    try:
        return json.loads(result.read_text())
    finally:
        result.unlink(missing_ok=True)


def run_doctor(ctx, rec: dict, data: Path) -> str:
    argv = ["podman", "run", *_BASE, "-v", f"{data}:/opt/data",
            "-e", "HERMES_HOME=/opt/data", "-e", "HOME=/opt/data",
            "--entrypoint", "/opt/hermes/.venv/bin/hermes", rec["id"], "doctor"]
    r = ctx.sh.run(argv, check=False, timeout=300)
    return r.stdout + r.stderr

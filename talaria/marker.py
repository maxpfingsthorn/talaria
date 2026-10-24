from __future__ import annotations

import json
from datetime import datetime

from talaria.state import write_json_atomic


def write(paths, op: str, backup: str, image: dict, now: datetime,
          *, restore_backup: str | None = None) -> None:
    record = {"op": op, "backup": backup, "image": image, "written": now.isoformat()}
    if restore_backup is not None:
        record["restore_backup"] = restore_backup
    write_json_atomic(paths.marker, record)


def read(paths) -> dict | None:
    try:
        return json.loads(paths.marker.read_text())
    except FileNotFoundError:
        return None


def clear(paths) -> None:
    paths.marker.unlink(missing_ok=True)

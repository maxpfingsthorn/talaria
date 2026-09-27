from __future__ import annotations

import json
from datetime import datetime

from talaria.state import write_json_atomic


def write(paths, op: str, backup: str, image: dict, now: datetime) -> None:
    write_json_atomic(paths.marker, {"op": op, "backup": backup, "image": image,
                                     "written": now.isoformat()})


def read(paths) -> dict | None:
    try:
        return json.loads(paths.marker.read_text())
    except FileNotFoundError:
        return None


def clear(paths) -> None:
    paths.marker.unlink(missing_ok=True)

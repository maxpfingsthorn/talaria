from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Found:
    unit: str
    container: str
    name: str
    image_id: str
    mounts: list
    env: dict
    quadlet: Path | None


def detect(ctx) -> list[Found]:
    return []

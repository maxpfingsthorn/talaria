from __future__ import annotations

from talaria.apps.base import App

NAMES = ("hermes",)


def get(name: str) -> App:
    if name == "hermes":
        from talaria.apps.hermes import APP
        return APP
    raise ValueError(f"unknown app: {name!r}")

from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

from talaria.disk import dir_size
from talaria.hermes import config_version
from talaria.state import ensure_dir, write_json_atomic

ID_RE = re.compile(r"^\d{8}T\d{6}Z-[a-z0-9][a-z0-9.-]{0,47}$")
KEEP_ALWAYS = "backups/config"


class BackupError(Exception):
    pass


@dataclass
class Backup:
    id: str
    path: Path
    meta: dict


def excluded(rel: str, excludes) -> bool:
    if rel == KEEP_ALWAYS or rel.startswith(KEEP_ALWAYS + "/") \
            or KEEP_ALWAYS.startswith(rel + "/"):
        return False
    return any(rel == e or rel.startswith(e + "/") for e in excludes)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def create(ctx, label: str, image: dict | None) -> Backup:
    root = ctx.paths.backups
    ensure_dir(root)
    stamp = ctx.now().strftime("%Y%m%dT%H%M%SZ")
    bid, n = f"{stamp}-{label}", 2
    while (root / f"{bid}.tar.gz").exists() or (root / f"{bid}.json").exists():
        bid, n = f"{stamp}-{label}-{n}", n + 1
    if not ID_RE.match(bid):
        raise BackupError(f"bad backup label: {label!r}")
    data = ctx.conf.data_dir
    excludes = ctx.conf.backup_exclude

    def keep(ti):
        rel = "" if ti.name == "." else ti.name[2:]
        if rel and excluded(rel, excludes):
            return None
        return ti if (ti.isreg() or ti.isdir() or ti.issym()) else None

    final = root / f"{bid}.tar.gz"
    tmp = root / f".{bid}.tar.gz.tmp"
    try:
        with tarfile.open(tmp, "w:gz") as tar:
            tar.add(str(data), arcname=".", filter=keep)
        with tarfile.open(tmp, "r:gz") as tar:
            for _ in tar:
                pass
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)
    meta = {"id": bid, "label": label, "created": ctx.now().isoformat(), "image": image,
            "cfg_version": config_version(data), "sha256": _sha256(final),
            "size": final.stat().st_size, "data_size": dir_size(data)}
    write_json_atomic(root / f"{bid}.json", meta)
    return Backup(bid, final, meta)


def list_backups(ctx) -> list[Backup]:
    root = ctx.paths.backups
    out = []
    for side in root.glob("*.json") if root.exists() else []:
        bid = side.name[:-5]
        arc = root / f"{bid}.tar.gz"
        if ID_RE.match(bid) and arc.exists():
            try:
                out.append(Backup(bid, arc, json.loads(side.read_text())))
            except ValueError:
                continue
    return sorted(out, key=lambda b: (b.meta.get("created", ""), b.id), reverse=True)


def get(ctx, bid: str) -> Backup:
    if not ID_RE.match(bid):
        raise KeyError(bid)
    for b in list_backups(ctx):
        if b.id == bid:
            return b
    raise KeyError(bid)


def verify(b: Backup) -> None:
    if _sha256(b.path) != b.meta.get("sha256"):
        raise BackupError(f"backup {b.id} is damaged (checksum mismatch)")


def prune(ctx, protect: set) -> list[str]:
    removed = []
    for i, b in enumerate(list_backups(ctx)):
        if i < ctx.conf.backup_keep or b.id in protect:
            continue
        b.path.unlink(missing_ok=True)
        (ctx.paths.backups / f"{b.id}.json").unlink(missing_ok=True)
        removed.append(b.id)
    for stray in ctx.paths.backups.glob(".*.tmp"):
        stray.unlink(missing_ok=True)
    return removed

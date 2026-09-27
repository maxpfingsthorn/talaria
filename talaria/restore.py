from __future__ import annotations

import os
import shutil
from pathlib import Path

from talaria.backup import verify

DONE = ".talaria-restore-complete"


def _names(data: Path, bid: str) -> tuple[Path, Path]:
    return (data.with_name(f"{data.name}.restore-{bid}"),
            data.with_name(f"{data.name}.old-{bid}"))


def _step_extract(ctx, b, new: Path) -> None:
    if new.exists():
        shutil.rmtree(new)
    verify(b)
    new.mkdir(mode=0o700)
    ctx.sh.run(["tar", "-xzf", str(b.path), "-C", str(new)], timeout=3600)
    (new / DONE).touch()


def _step_rename_old(ctx, data: Path, old: Path) -> None:
    os.rename(data, old)


def _step_rename_new(ctx, new: Path, data: Path) -> None:
    os.rename(new, data)


def _move_missing(src: Path, dst: Path) -> None:
    if not os.path.lexists(src):
        return
    if not os.path.lexists(dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dst)
    elif src.is_dir() and not src.is_symlink() and dst.is_dir() and not dst.is_symlink():
        for child in src.iterdir():
            _move_missing(child, dst / child.name)


def _step_move_excluded(ctx, old: Path, data: Path) -> None:
    for ex in ctx.conf.backup_exclude:
        _move_missing(old / ex, data / ex)


class RestoreError(Exception):
    pass


def _remove(old: Path) -> None:
    shutil.rmtree(old, ignore_errors=True)
    if os.path.lexists(old):
        raise RestoreError(f"cannot remove the leftover directory {old}")


def restore_data(ctx, b) -> None:
    data = Path(ctx.conf.data_dir)
    new, old = _names(data, b.id)
    if old.exists() and data.exists() and not (data / DONE).exists():
        # a finished swap (or a leftover of one): complete it, then restore from scratch
        _step_move_excluded(ctx, old, data)
        _remove(old)
    if not old.exists():
        if not (new / DONE).exists():
            _step_extract(ctx, b, new)
        _step_rename_old(ctx, data, old)
    if not data.exists():
        _step_rename_new(ctx, new, data)
    (data / DONE).unlink(missing_ok=True)
    _step_move_excluded(ctx, old, data)
    shutil.rmtree(old, ignore_errors=True)   # a leftover is handled at the next restore

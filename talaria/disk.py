from __future__ import annotations

import os
from pathlib import Path

GB = 1024 ** 3


class NoSpace(Exception):
    pass


def dir_size(path) -> int:
    total = 0
    stack = [str(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        total += e.stat(follow_symlinks=False).st_size
        except (FileNotFoundError, PermissionError):
            continue
    return total


def free_bytes(path) -> int:
    p = Path(path)
    while not p.exists():
        p = p.parent
    st = os.statvfs(p)
    return st.f_bavail * st.f_frsize


def ensure_space(ctx, need_bytes: int, where) -> None:
    need = need_bytes + int(ctx.conf.disk_floor_gb * GB)
    have = free_bytes(where)
    if have < need:
        raise NoSpace(f"need {need / GB:.1f} GB free on {where}, have {have / GB:.1f} GB")

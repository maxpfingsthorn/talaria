"""Writes into directories an agent controls must never follow a symlink."""
from __future__ import annotations

import os
import secrets
import shutil
import stat
from pathlib import Path

_NOFOLLOW_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW


def write_text(path: Path, text: str, mode: int = 0o600) -> None:
    """Atomically replace path with a regular file; a symlink at path is replaced, not written through."""
    path = Path(path)
    if path.parent.is_symlink():
        raise ValueError(f"{path.parent} is a symlink")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        fd = os.open(tmp, _NOFOLLOW_NEW, mode)
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)  # the umask may have narrowed the creation mode
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def is_regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def copy_regular(src, dst, *, follow_symlinks: bool = True) -> str:
    """copy_function for shutil.copytree: refuses anything but a regular file at src."""
    sfd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(sfd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"not a regular file: {src}")
        dfd = os.open(dst, _NOFOLLOW_NEW, stat.S_IMODE(st.st_mode))
        try:
            with os.fdopen(os.dup(sfd), "rb") as fin, os.fdopen(dfd, "wb", closefd=False) as fout:
                shutil.copyfileobj(fin, fout)
            os.fchmod(dfd, stat.S_IMODE(st.st_mode))
            os.utime(dfd, ns=(st.st_atime_ns, st.st_mtime_ns))
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)
    return str(dst)

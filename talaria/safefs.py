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
            os.fchmod(f.fileno(), mode)  # on the fd: the umask may have narrowed the creation mode
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
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
    """Copy the regular file at src to the new file dst; refuses anything else at src."""
    return _copy_regular(src, None, dst)


def copy_regular_at(dir_fd: int, name: str, dst) -> str:
    """copy_regular for the entry `name` of an open directory: no path is re-resolved."""
    return _copy_regular(name, dir_fd, dst)


def _copy_regular(src, dir_fd, dst) -> str:
    # O_NONBLOCK: opening a FIFO that was swapped in after the scan must not wait for a writer
    sfd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    try:
        st = os.fstat(sfd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"not a regular file: {src}")
        os.set_blocking(sfd, True)
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

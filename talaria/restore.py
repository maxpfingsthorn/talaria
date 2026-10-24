from __future__ import annotations

import errno
import os
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path

from talaria import safefs
from talaria.backup import verify

DONE = ".talaria-restore-complete"   # legacy in-data marker; never trusted as completion evidence


def _names(data: Path, bid: str) -> tuple[Path, Path]:
    return (data.with_name(f"{data.name}.restore-{bid}"),
            data.with_name(f"{data.name}.old-{bid}"))


def _completion(new: Path) -> Path:
    # A sibling of the data dir is outside the app's mount and outside its archive.
    return new.with_name(new.name + ".complete")


def _step_extract(ctx, b, new: Path) -> None:
    _completion(new).unlink(missing_ok=True)
    if new.exists():
        shutil.rmtree(new)
    verify(b)
    new.mkdir(mode=0o700)  # pragma: no mutate  (tar then applies the archived mode of ".")
    ctx.sh.run(["tar", "-xzf", str(b.path), "-C", str(new)], timeout=3600)
    safefs.write_text(_completion(new), "complete\n")


def _step_rename_old(ctx, data: Path, old: Path) -> None:
    os.rename(data, old)


def _step_rename_new(ctx, new: Path, data: Path) -> None:
    os.rename(new, data)


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextmanager
def _parent_fd(root: Path, path: Path, *, create: bool = False):
    """Hold every descent beneath root to a real directory, even if a path is swapped."""
    fd = None
    try:
        fd = os.open(root, _DIR_FLAGS)
        for part in path.parent.relative_to(root).parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
    except OSError as e:
        if fd is not None:
            os.close(fd)
            fd = None
        if e.errno not in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
            raise
    try:
        yield fd
    finally:
        if fd is not None:
            os.close(fd)


def _move_missing_at(src_fd: int, src: str, dst_fd: int, dst: str) -> None:
    try:
        source = os.stat(src, dir_fd=src_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    try:
        dest = os.stat(dst, dir_fd=dst_fd, follow_symlinks=False)
    except FileNotFoundError:
        os.rename(src, dst, src_dir_fd=src_fd, dst_dir_fd=dst_fd)
        return
    if stat.S_ISDIR(source.st_mode) and stat.S_ISDIR(dest.st_mode):
        sfd = os.open(src, _DIR_FLAGS, dir_fd=src_fd)
        try:
            dfd = os.open(dst, _DIR_FLAGS, dir_fd=dst_fd)
            try:
                for name in os.listdir(sfd):
                    _move_missing_at(sfd, name, dfd, name)
            finally:
                os.close(dfd)
        finally:
            os.close(sfd)


def _move_missing(src: Path, dst: Path, root: Path, *, source_root: Path | None = None) -> None:
    with _parent_fd(source_root or src.parent, src) as sfd:
        if sfd is None:
            return
        try:
            os.stat(src.name, dir_fd=sfd, follow_symlinks=False)
        except FileNotFoundError:
            return
        with _parent_fd(root, dst, create=True) as dfd:
            if dfd is not None:
                _move_missing_at(sfd, src.name, dfd, dst.name)


def _step_move_excluded(ctx, old: Path, data: Path) -> None:
    for ex in ctx.conf.backup_exclude:
        _move_missing(old / ex, data / ex, data, source_root=old)


class RestoreError(Exception):
    pass


def _remove(old: Path) -> None:
    shutil.rmtree(old, ignore_errors=True)
    if os.path.lexists(old):
        raise RestoreError(f"cannot remove the leftover directory {old}")


def restore_data(ctx, b) -> None:
    data = Path(ctx.conf.data_dir)
    new, old = _names(data, b.id)
    complete = _completion(new)
    if old.exists() and data.exists() and not complete.exists():
        # a finished swap (or a leftover of one): complete it, then restore from scratch
        _step_move_excluded(ctx, old, data)
        _remove(old)
    if not old.exists():
        if not (complete.exists() and new.is_dir()):   # the evidence alone is no extraction
            _step_extract(ctx, b, new)
        _step_rename_old(ctx, data, old)  # pragma: no mutate  (step ignores ctx)
    if not data.exists():
        _step_rename_new(ctx, new, data)  # pragma: no mutate  (step ignores ctx)
    complete.unlink(missing_ok=True)
    _step_move_excluded(ctx, old, data)
    shutil.rmtree(old, ignore_errors=True)   # a leftover is handled at the next restore

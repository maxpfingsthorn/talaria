from __future__ import annotations

import errno
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

from talaria import disk, safefs, service, state
from talaria.backup import excluded
from talaria.images import RevisionMismatch
from talaria.notify import Message
from talaria.shell import CommandError
from talaria.state import ensure_dir

SQLITE_MAGIC = b"SQLite format 3\x00"


class Transient(Exception):
    pass


class NeedsRecovery(Transient):
    """The app was left stopped: the next check skips it until /rollback CONFIRM."""


class Permanent(Exception):
    def __init__(self, msg: str, details=()):
        super().__init__(msg)
        self.details = list(details)


_SWAPPED = (errno.ELOOP, errno.ENOTDIR, errno.ENOENT, errno.EINVAL)


def _copy_dir(fd: int, dst: str, rel: str, excludes) -> None:
    """Copy the directory open as `fd` into the new directory `dst`. The data dir is writable
    by the app while this runs: every descent and open goes through an fd with O_NOFOLLOW,
    so a component swapped for a symlink since it was listed is skipped, never followed."""
    for entry in list(os.scandir(fd)):
        name = entry.name
        sub = name if not rel else f"{rel}/{name}"
        if excluded(sub, excludes) or name.endswith(("-wal", "-shm")):
            continue
        try:
            mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
        except FileNotFoundError:        # production is running: files come and go
            continue
        target = os.path.join(dst, name)
        try:
            if stat.S_ISDIR(mode):
                cfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    st = os.fstat(cfd)
                    os.mkdir(target, 0o700)
                    _copy_dir(cfd, target, sub, excludes)
                    os.chmod(target, stat.S_IMODE(st.st_mode))
                    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
                finally:
                    os.close(cfd)
            elif stat.S_ISREG(mode):
                safefs.copy_regular_at(fd, name, target)
            elif stat.S_ISLNK(mode):
                os.symlink(os.readlink(name, dir_fd=fd), target)
        except OSError as e:
            if e.errno not in _SWAPPED:
                raise


def copy_data(src: Path, dst: Path, excludes) -> None:
    src = Path(src)
    fd = os.open(src, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        os.makedirs(dst, mode=0o700)
        _copy_dir(fd, str(dst), "", excludes)
        os.chmod(dst, stat.S_IMODE(st.st_mode))
        os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
    finally:
        os.close(fd)
    for root, _dirs, files in os.walk(dst):
        for f in files:
            p = Path(root) / f
            if p.is_symlink() or not p.is_file():
                continue
            with open(p, "rb") as fh:
                if fh.read(16) != SQLITE_MAGIC:
                    continue
            _sqlite_copy(src / p.relative_to(dst), p, src)


def _real_dirs(root: Path, path: Path) -> bool:
    """Every component from root down to path's parent is a directory, not a symlink."""
    cur = root
    for part in path.parent.relative_to(root).parts:
        cur = cur / part
        try:
            if not stat.S_ISDIR(os.lstat(cur).st_mode):
                return False
        except OSError:
            return False
    return True


def _holds(ident: tuple, skip: int) -> bool:
    """Some fd of this process other than `skip` is open on the file `ident` (dev, ino)."""
    for name in os.listdir("/proc/self/fd"):
        try:
            fd = int(name)
            st = os.fstat(fd) if fd != skip else None
        except (ValueError, OSError):
            continue
        if st is not None and (st.st_dev, st.st_ino) == ident:
            return True
    return False


def _sqlite_copy(src: Path, dst: Path, root: Path) -> None:
    dst.unlink()
    if not _real_dirs(root, src):        # O_NOFOLLOW guards only the last component
        raise OSError(f"{src} changed during the copy")
    # O_NONBLOCK: a FIFO swapped in since the scan would block sqlite (and open)
    fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise OSError(f"not a regular file: {src}")
        # equivalent mutants: sqlite accepts file: URIs without uri=True; PRAGMA is case-insensitive
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)  # pragma: no mutate
        d = sqlite3.connect(dst)
        try:
            s.execute("PRAGMA trusted_schema=OFF")  # pragma: no mutate
            s.execute("PRAGMA schema_version").fetchone()     # makes sqlite open the file
            # sqlite opens by path: the file it holds must be the one checked above, or a
            # directory swapped and swapped back around connect() brought in another one
            if not _holds((before.st_dev, before.st_ino), fd):
                d.close()
                dst.unlink(missing_ok=True)
                raise OSError(f"{src} changed during the copy")
            s.backup(d)
        finally:
            d.close()
            s.close()
        now = os.lstat(src)
        if (not stat.S_ISREG(now.st_mode) or (now.st_dev, now.st_ino) != (before.st_dev, before.st_ino)
                or not _real_dirs(root, src)):
            dst.unlink(missing_ok=True)
            raise OSError(f"{src} changed during the copy")
    finally:
        os.close(fd)


def candidate_message(ctx, st, report: dict, replaced: str | None) -> Message:
    tag = report["tag"]
    cur = (st.get("current") or {}).get("tag") or "unknown"
    lines = [f"{ctx.app.title} {tag} is ready to deploy (current {cur}). The rehearsal on a copy passed."]
    if ctx.conf.repo.startswith("https://github.com/"):
        lines.append(f"Release notes: {ctx.conf.repo}/releases/tag/{tag}")
    app_lines, blocks = ctx.app.report_lines(ctx, report)
    lines += app_lines
    provenance = ((st.get("pending") or {}).get("image") or {}).get("provenance") or ""
    if provenance.startswith("sha256 only"):
        lines.append(f"Build provenance not verified: {provenance[len('sha256 only ('):-1]}.")
    if replaced:
        lines.append(f"Replaces the pending {replaced}.")
    return Message("\n".join(lines), untrusted=blocks,
                   commands=[f"/approve {tag}", f"/reject {tag}"],
                   buttons=[[(f"Approve {tag}", f"ap:{tag}"), ("Reject", f"rj:{tag}")]])


def rehearse(ctx, st: dict, tag: str, commit: str) -> None:
    try:
        image = ctx.app.fetch(ctx, tag, commit)
    except RevisionMismatch as e:
        raise Permanent(str(e)) from None
    except CommandError as e:
        raise Transient(f"{ctx.app.fetch_error}: {e}") from None
    data = ctx.conf.data_dir
    try:
        disk.ensure_space(ctx, disk.dir_size(data), ctx.paths.state_dir)
    except disk.NoSpace as e:
        raise Transient(str(e)) from None
    shutil.rmtree(ctx.paths.staging, ignore_errors=True)  # leftovers of a crashed rehearsal
    ensure_dir(ctx.paths.staging)
    stage = ctx.paths.staging / f"{tag}-{ctx.now():%Y%m%dT%H%M%SZ}"
    stage.mkdir(mode=0o700)
    copy = stage / "data"
    try:
        # an app whose files are only consistent at rest (gbrain's PGLite) is stopped
        # for the copy; seconds of downtime
        stopped = ctx.app.copy_stopped and service.is_active(ctx)
        try:
            if stopped:
                # Persist before stopping: SIGKILL/OOM bypasses the restart in finally.
                st["op"] = {"op": "rehearse", "changed": False,
                            "started": ctx.now().isoformat()}
                state.save(ctx.paths, st)
                try:
                    service.stop(ctx)
                except (CommandError, subprocess.TimeoutExpired) as e:
                    raise Transient(f"could not stop {ctx.app.title}: {str(e)[:300]}") from None
            copy_data(data, copy, ctx.conf.backup_exclude)
        except (OSError, shutil.Error, sqlite3.Error) as e:
            raise Transient(f"could not copy the data dir: {str(e)[:300]}") from None
        finally:
            if stopped:
                earlier = sys.exc_info()[1]     # the error this finally is running for
                try:
                    service.start(ctx)
                except Exception as e:
                    # the bot's /rollback CONFIRM starts it; nothing was changed
                    st["op"] = {"op": "rehearse", "changed": False,
                                "started": ctx.now().isoformat()}
                    state.save(ctx.paths, st)
                    more = f" Earlier error: {str(earlier)[:300]}." if earlier else ""
                    raise NeedsRecovery(f"{ctx.app.title} is not running after the rehearsal copy "
                                    f"({str(e)[:300]}). Send /rollback CONFIRM to start it "
                                    f"again.{more}") from None
                st["op"] = None
                state.save(ctx.paths, st)
        report = ctx.app.rehearse(ctx, st, image, copy, stage)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    old = st.get("pending")
    replaced = old["tag"] if old and old.get("tag") != tag else None
    extra = ctx.app.pending_extra(report)
    collide = {"tag", "image", "report"} & extra.keys()
    if collide:
        raise ValueError(f"app.pending_extra() overwrote core pending key(s): {sorted(collide)}")
    st["pending"] = {"tag": tag, "image": image, "report": report, **extra}
    ctx.notify.send(candidate_message(ctx, st, report, replaced))

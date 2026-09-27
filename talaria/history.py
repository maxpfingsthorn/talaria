from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

from talaria.notify import Message
from talaria.shell import CommandError
from talaria.state import ensure_dir


def _wanted(data: Path) -> list[str]:
    out = []
    candidates = ["config.yaml"]
    mem = data / "memories"
    if mem.is_dir() and not mem.is_symlink():
        candidates += [f"memories/{p.name}" for p in sorted(mem.iterdir()) if p.suffix == ".md"]
    for rel in candidates:
        try:
            if stat.S_ISREG(os.lstat(data / rel).st_mode):
                out.append(rel)
        except FileNotFoundError:
            pass
    return out


def _copy_regular(src: Path, dst: Path) -> None:
    """Copy src to dst without following a symlink on either side.

    The data dir is agent-writable: src may be swapped for a symlink after _wanted()
    checked it, and dst may be a symlink planted in the repo earlier. Reading uses
    O_NOFOLLOW + fstat; writing goes to a new temp file that replaces dst.
    """
    try:
        fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:
        if e.errno == errno.ELOOP:          # became a symlink: skip it this time
            return
        raise
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            return
        content = f.read()
    tmp = dst.with_name(f".{dst.name}.talaria-tmp")
    tmp.unlink(missing_ok=True)
    wfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(wfd, "wb") as f:
        f.write(content)
    os.replace(tmp, dst)


def _git(ctx, *args):
    return ctx.sh.run(["git", "-C", str(ctx.paths.history), *args], timeout=120)


def commit(ctx, st: dict, message: str) -> None:
    repo, data = ctx.paths.history, ctx.conf.data_dir
    try:
        if not (repo / ".git").exists():
            ensure_dir(repo)
            _git(ctx, "init", "-q")
        wanted = set(_wanted(data))
        for p in list(repo.rglob("*")):
            rel = p.relative_to(repo).as_posix()
            if rel.startswith(".git") or (p.is_dir() and not p.is_symlink()):
                continue
            if rel not in wanted:
                p.unlink()
        for rel in wanted:
            # equivalent mutant: parents only matters below memories/, which is flat
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)  # pragma: no mutate
            _copy_regular(data / rel, repo / rel)
        _git(ctx, "add", "-A")
        if _git(ctx, "status", "--porcelain").stdout.strip():
            _git(ctx, "-c", "user.name=Talaria", "-c", "user.email=talaria@localhost",
                 "commit", "-qm", message)
        st["history_error"] = None
    except (CommandError, OSError) as e:
        if st.get("history_error") != str(e):
            ctx.notify.send(Message(f"History commit failed: {e}"))
        st["history_error"] = str(e)

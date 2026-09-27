from __future__ import annotations

import os
import shutil
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
            if rel.startswith(".git") or p.is_dir():
                continue
            if rel not in wanted:
                p.unlink()
        for rel in wanted:
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(data / rel, repo / rel, follow_symlinks=False)
        _git(ctx, "add", "-A")
        if _git(ctx, "status", "--porcelain").stdout.strip():
            _git(ctx, "-c", "user.name=Talaria", "-c", "user.email=talaria@localhost",
                 "commit", "-qm", message)
        st["history_error"] = None
    except (CommandError, OSError) as e:
        if st.get("history_error") != str(e):
            ctx.notify.send(Message(f"History commit failed: {e}"))
        st["history_error"] = str(e)

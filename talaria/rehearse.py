from __future__ import annotations

import os
import shutil
import sqlite3
import stat
from pathlib import Path

from talaria import disk
from talaria.backup import excluded
from talaria.images import RevisionMismatch
from talaria.notify import Message
from talaria.shell import CommandError
from talaria.state import ensure_dir

SQLITE_MAGIC = b"SQLite format 3\x00"


class Transient(Exception):
    pass


class Permanent(Exception):
    def __init__(self, msg: str, details=()):
        super().__init__(msg)
        self.details = list(details)


def copy_data(src: Path, dst: Path, excludes) -> None:
    src = Path(src)

    def ignore(dirpath, names):
        rel_dir = os.path.relpath(dirpath, src)
        skip = []
        for n in names:
            rel = n if rel_dir == "." else f"{rel_dir}/{n}"
            try:
                mode = os.lstat(os.path.join(dirpath, n)).st_mode
            except FileNotFoundError:        # production is running: files come and go
                skip.append(n)
                continue
            if (excluded(rel, excludes) or n.endswith(("-wal", "-shm"))
                    or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode) or stat.S_ISLNK(mode))):
                skip.append(n)
        return skip

    shutil.copytree(src, dst, symlinks=True, ignore=ignore)
    for root, dirs, files in os.walk(dst):
        for f in files:
            p = Path(root) / f
            if p.is_symlink() or not p.is_file():
                continue
            with open(p, "rb") as fh:
                if fh.read(16) != SQLITE_MAGIC:
                    continue
            _sqlite_copy(src / p.relative_to(dst), p)


def _sqlite_copy(src: Path, dst: Path) -> None:
    dst.unlink()
    # equivalent mutants: sqlite accepts file: URIs without uri=True; PRAGMA is case-insensitive
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)  # pragma: no mutate
    d = sqlite3.connect(dst)
    try:
        s.execute("PRAGMA trusted_schema=OFF")  # pragma: no mutate
        s.backup(d)
    finally:
        d.close()
        s.close()


def candidate_message(ctx, st, report: dict, replaced: str | None) -> Message:
    tag = report["tag"]
    cur = (st.get("current") or {}).get("tag") or "unknown"
    lines = [f"{ctx.app.title} {tag} is ready to deploy (current {cur}). The rehearsal on a copy passed."]
    if ctx.conf.hermes_repo.startswith("https://github.com/"):
        lines.append(f"Release notes: {ctx.conf.hermes_repo}/releases/tag/{tag}")
    app_lines, blocks = ctx.app.report_lines(ctx, report)
    lines += app_lines
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
        raise Transient(f"pull failed: {e}") from None
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
        try:
            copy_data(data, copy, ctx.conf.backup_exclude)
        except (OSError, shutil.Error, sqlite3.Error) as e:
            raise Transient(f"could not copy the data dir: {str(e)[:300]}") from None
        report = ctx.app.rehearse(ctx, st, image, copy, stage)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    old = st.get("pending")
    replaced = old["tag"] if old and old.get("tag") != tag else None
    st["pending"] = {"tag": tag, "image": image, "report": report, **ctx.app.pending_extra(report)}
    ctx.notify.send(candidate_message(ctx, st, report, replaced))

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
from pathlib import Path

from talaria import disk
from talaria.backup import excluded
from talaria.containers import HelperError, run_doctor, run_helper
from talaria.images import RevisionMismatch, pull_verify
from talaria.notify import Message
from talaria.shell import CommandError
from talaria.state import ensure_dir

SQLITE_MAGIC = b"SQLite format 3\x00"
EMPTY_DIFF = {"ok": True, "changed": [], "added": [], "removed": [], "error": None}


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


def _doctor_changes(before: str, after: str) -> list[str]:
    b, a = before.splitlines(), after.splitlines()
    out = [f"+ {l}" for l in a if l.strip() and l not in b]
    out += [f"- {l}" for l in b if l.strip() and l not in a]
    return out[:40]


def _fmt_diff(d: dict) -> str:
    lines = [f"~ {k}: {o} → {n}" for k, o, n in d.get("changed", [])]
    lines += [f"+ {k}: {v}" for k, v in d.get("added", [])]
    lines += [f"- {k}: {v}" for k, v in d.get("removed", [])]
    return "\n".join(lines)


def candidate_message(ctx, st, report: dict, replaced: str | None) -> Message:
    tag = report["tag"]
    cur = (st.get("current") or {}).get("tag") or "unknown"
    lines = [f"Hermes {tag} is ready to deploy (current {cur}). The rehearsal on a copy passed."]
    if ctx.conf.hermes_repo.startswith("https://github.com/"):
        lines.append(f"Release notes: {ctx.conf.hermes_repo}/releases/tag/{tag}")
    lines.append(f"Config version: {report['cfg_before']} → {report['cfg_after']}")
    db = report["db"]
    held = db["after"] is not None and db["schema_version"] and db["after"] < db["schema_version"]
    lines.append(f"state.db: {db['before']} → {db['after']}"
                 + (f" (held back; image supports {db['schema_version']})" if held else ""))
    if replaced:
        lines.append(f"Replaces the pending {replaced}.")
    blocks = [b for b in ("\n".join(report["messages"]), _fmt_diff(report["diff"]),
                          "\n".join(report["doctor"])) if b]
    return Message("\n".join(lines), untrusted=blocks,
                   commands=[f"/approve {tag}", f"/reject {tag}"])


def rehearse(ctx, st: dict, tag: str, commit: str) -> None:
    try:
        image = pull_verify(ctx, tag, commit)
    except RevisionMismatch as e:
        raise Permanent(str(e)) from None
    except CommandError as e:
        raise Transient(f"pull failed: {e}") from None
    data = ctx.conf.data_dir
    try:
        disk.ensure_space(ctx, disk.dir_size(data), ctx.paths.state_dir)
    except disk.NoSpace as e:
        raise Transient(str(e)) from None
    ensure_dir(ctx.paths.staging)
    stage = ctx.paths.staging / f"{tag}-{ctx.now():%Y%m%dT%H%M%SZ}"
    stage.mkdir(mode=0o700)
    copy = stage / "data"
    try:
        try:
            copy_data(data, copy, ctx.conf.backup_exclude)
        except (OSError, shutil.Error, sqlite3.Error) as e:
            raise Transient(f"could not copy the data dir: {str(e)[:300]}") from None
        has_cfg = (copy / "config.yaml").is_file()
        if has_cfg:
            shutil.copy2(copy / "config.yaml", stage / "config.orig.yaml")
        doc_before = run_doctor(ctx, st["current"], copy) if st.get("current") else ""
        mig = run_helper(ctx, image, "migrate.py", copy, stage)
        if not mig["ok"]:
            raise Permanent(f"config migration failed: {mig['error']}", mig["messages"])
        db = run_helper(ctx, image, "dbopen.py", copy, stage)
        if not db["ok"]:
            raise Permanent(f"state.db could not be opened: {db['error']}")
        doc_after = run_doctor(ctx, image, copy)
        diff = run_helper(ctx, image, "confdiff.py", copy, stage,
                          args=["/opt/talaria-out/config.orig.yaml", "/opt/data/config.yaml"]) \
            if has_cfg else EMPTY_DIFF
    except HelperError as e:
        raise Permanent(str(e)) from None
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    report = {"tag": tag, "digest": image["digest"], "cfg_before": mig["before"],
              "cfg_after": mig["after"], "db": db, "messages": mig["messages"],
              "diff": diff, "doctor": _doctor_changes(doc_before, doc_after)}
    old = st.get("pending")
    replaced = old["tag"] if old and old.get("tag") != tag else None
    st["pending"] = {"tag": tag, "image": image, "cfg_after": mig["after"], "report": report}
    ctx.notify.send(candidate_message(ctx, st, report, replaced))

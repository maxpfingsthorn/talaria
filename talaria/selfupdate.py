from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from talaria import lock
from talaria.cli import EXIT_BUSY
from talaria.hubexec import parse_lines
from talaria.shell import CommandError
from talaria.state import ensure_dir


def self_update(ctx, tag: str) -> int:
    """Switch this install to `tag` and let the new code re-render its units (it restarts
    the app only if its Quadlet changed). Never under a running operation: that one would
    continue on half-old, half-new code, so the op lock is held from before the fetch until
    the new setup is done (setup is told not to take it again). If setup fails, the
    previous checkout is restored."""
    d = str(ctx.paths.install_dir)
    try:
        with lock.op_lock(ctx.paths):
            try:
                prev = ctx.sh.run(["git", "-C", d, "rev-parse", "HEAD"]).stdout.strip()
                ctx.sh.run(["git", "-C", d, "fetch", "-q", "--tags", "origin"], timeout=600)
                ctx.sh.run(["git", "-C", d, "checkout", "-q", tag])
            except CommandError as e:
                print(f"self-update failed: {e}", file=sys.stderr)
                return 1
            r = ctx.sh.run([str(ctx.paths.bin_link), "setup", "--as-service", "--lock-held"],
                           check=False, timeout=1800)
            print(r.stdout, end="")
            if r.returncode != 0:
                ctx.sh.run(["git", "-C", d, "checkout", "-q", prev], check=False)
                print(f"self-update to {tag} failed; previous checkout restored", file=sys.stderr)
                return 1
    except lock.Busy:
        print(f"self-update to {tag} refused: an operation is running", file=sys.stderr)
        return EXIT_BUSY
    print(f"Talaria {tag} installed.")
    return 0


def dry_run(ctx, tag: str) -> bool:
    """Would updating to `tag` change this app's Quadlet, and so restart the app? The new
    version renders it with its own code in a throwaway worktree; nothing else changes."""
    d = str(ctx.paths.install_dir)
    ensure_dir(ctx.paths.state_dir)
    wt = Path(tempfile.mkdtemp(prefix="dry-run-", dir=ctx.paths.state_dir))   # one per run
    ctx.sh.run(["git", "-C", d, "fetch", "-q", "--tags", "origin"], timeout=600)
    shutil.rmtree(wt, ignore_errors=True)  # pragma: no mutate  (the fresh dir is removable; the effect is pinned by test_dry_run_details)
    ctx.sh.run(["git", "-C", d, "worktree", "prune"], check=False)
    ctx.sh.run(["git", "-C", d, "worktree", "add", "-q", "--detach", str(wt), tag], timeout=120)
    try:
        r = ctx.sh.run([str(wt / "bin/talaria"), "op", "quadlet"], timeout=120)
    finally:
        ctx.sh.run(["git", "-C", d, "worktree", "remove", "--force", str(wt)], check=False)
        shutil.rmtree(wt, ignore_errors=True)
    new = next((x["text"] for x in parse_lines(r.stdout, ctx.app.name)
                if x.get("kind") == "reply" and isinstance(x.get("text"), str)), None)
    if new is None:
        raise ValueError(f"Talaria {tag} did not render a Quadlet")
    old = ctx.paths.quadlet.read_text() if ctx.paths.quadlet.exists() else None
    return new != old

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from talaria import lock, trust
from talaria.cli import EXIT_BUSY
from talaria.hubexec import parse_lines
from talaria.shell import CommandError
from talaria.state import ensure_dir


def fetch_tag(sh, install_dir: str, tag: str) -> None:
    """Fetch exactly `tag`, forced: a re-pointed upstream tag must not fail the fetch (as
    `--tags` does for a tag that moved) and break every later update."""
    sh.run(["git", "-C", install_dir, "fetch", "-q", "origin", f"+refs/tags/{tag}:refs/tags/{tag}"],
           timeout=600)


def tag_commit(sh, install_dir: str, tag: str) -> str:
    """The commit `tag` points to in this install (CommandError if it has no such tag)."""
    return sh.run(["git", "-C", install_dir, "rev-parse", "--verify", "-q",
                   f"refs/tags/{tag}^{{commit}}"]).stdout.strip()


def self_update(ctx, tag: str, commit: str | None = None) -> int:
    """Switch this install to `tag` and let the new code re-render its units (it restarts
    the app only if its Quadlet changed). Never under a running operation: that one would
    continue on half-old, half-new code, so the op lock is held from before the fetch until
    the new setup is done (setup is told not to take it again). With `commit` (what the hub
    installed) the update is refused, before anything changes, if the fetched tag is another.
    If setup fails or times out, the previous checkout is restored and its setup re-run, so
    the units match it again."""
    d = str(ctx.paths.install_dir)
    try:
        with lock.op_lock(ctx.paths):
            try:
                prev = ctx.sh.run(["git", "-C", d, "rev-parse", "HEAD"]).stdout.strip()
                fetch_tag(ctx.sh, d, tag)
                trust.check(ctx.sh, d, tag, ctx.paths.state_dir)
                got = tag_commit(ctx.sh, d, tag) if commit else commit
                if got != commit:
                    print(f"self-update refused: {tag} is {got[:12]} here, the hub installed "
                          f"{commit[:12]}", file=sys.stderr)
                    return 1
                ctx.sh.run(["git", "-C", d, "checkout", "-q", tag])
            except trust.Untrusted as e:
                print(f"self-update refused: {tag} is not signed by a trusted key ({e})",
                      file=sys.stderr)
                return 1
            except CommandError as e:
                print(f"self-update failed: {e}", file=sys.stderr)
                return 1
            setup = [str(ctx.paths.bin_link), "setup", "--as-service", "--lock-held"]
            try:
                r = ctx.sh.run(setup, check=False, timeout=1800)
                ok, out = r.returncode == 0, r.stdout
            except subprocess.TimeoutExpired:
                ok, out = False, ""
            print(out, end="")
            if not ok:
                ctx.sh.run(["git", "-C", d, "checkout", "-q", prev], check=False)
                try:   # bin_link now runs the old code: it re-renders the old units
                    ok2 = ctx.sh.run(setup, check=False, timeout=1800).returncode == 0
                except subprocess.TimeoutExpired:
                    ok2 = False
                tail = "" if ok2 else (f", but its setup failed too: {ctx.app.title} may be "
                                       "stopped, see /status")
                print(f"self-update to {tag} failed; previous version restored{tail}",
                      file=sys.stderr)
                return 1
    except lock.Busy:
        print(f"self-update to {tag} refused: an operation is running", file=sys.stderr)
        return EXIT_BUSY
    print(f"Talaria {tag} installed.")
    return 0


def dry_run(ctx, tag: str) -> bool:
    """Would updating to `tag` change this app's Quadlet, and so restart the app? The new
    version renders it with its own code in a throwaway worktree; nothing else changes.
    Raises `trust.Untrusted` if the tag is not signed by a trusted key (before any worktree)."""
    d = str(ctx.paths.install_dir)
    ensure_dir(ctx.paths.state_dir)
    wt = Path(tempfile.mkdtemp(prefix="dry-run-", dir=ctx.paths.state_dir))   # one per run
    fetch_tag(ctx.sh, d, tag)
    trust.check(ctx.sh, d, tag, ctx.paths.state_dir)
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

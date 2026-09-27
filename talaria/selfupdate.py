from __future__ import annotations

import sys

from talaria.shell import CommandError


def self_update(ctx, tag: str) -> int:
    d = str(ctx.paths.install_dir)
    try:
        ctx.sh.run(["git", "-C", d, "fetch", "-q", "--tags", "origin"], timeout=600)
        ctx.sh.run(["git", "-C", d, "checkout", "-q", tag])
    except CommandError as e:
        print(f"self-update failed: {e}", file=sys.stderr)
        return 1
    # the new code re-renders units and restarts Hermes only if its Quadlet changed
    r = ctx.sh.run([str(ctx.paths.bin_link), "setup", "--as-service"], check=False,
                   timeout=1800)
    print(r.stdout, end="")
    ctx.sh.run(["systemctl", "--user", "restart", "talaria-telegram.service"])
    print(f"Talaria {tag} installed.")
    return 0 if r.returncode == 0 else 1

"""Release-signature check: a tag is installed only if it is signed by a key listed in
`keys/allowed_signers` of the *verifying install's own HEAD* (never the working tree, never
the new tag, so a tag cannot vouch for itself). The decision is `git verify-tag`'s exit code;
its output only feeds the (short) reason."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import os
import tempfile
from pathlib import Path

NO_KEYS = "this install has no trusted release keys"
_KEY_TYPES = frozenset({"ssh-ed25519", "sk-ssh-ed25519@openssh.com", "ssh-rsa",
                        "sk-ecdsa-sha2-nistp256@openssh.com",
                        "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521"})


class Untrusted(Exception):
    """The tag is not signed by a trusted key; `str(e)` is a one-line reason."""


def trusted_signers(sh, install_dir: str) -> str:
    r = sh.run(["git", "-C", install_dir, "show", "HEAD:keys/allowed_signers"], check=False)
    if r.returncode != 0 or not r.stdout.strip():
        raise Untrusted(NO_KEYS)
    return r.stdout


def _reason(stderr: str) -> str:
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    return lines[-1][:200] if lines else "signature check failed"


def verify_tag(sh, install_dir: str, tag: str, signers: str, tmp_dir: Path | None = None) -> None:
    ref = f"refs/tags/{tag}"
    kind = sh.run(["git", "-C", install_dir, "cat-file", "-t", ref], check=False)
    if kind.returncode != 0:
        raise Untrusted(f"{tag} not found")
    if kind.stdout.strip() != "tag":
        # verify-tag would say little for these; a lightweight tag cannot carry a signature
        raise Untrusted(f"{tag} is a lightweight tag")
    fd, name = tempfile.mkstemp(prefix="allowed_signers.", dir=tmp_dir)   # mkstemp: mode 0600
    try:
        with os.fdopen(fd, "w") as f:
            f.write(signers)
        r = sh.run(["git", "-C", install_dir, "-c", "gpg.format=ssh", "-c", "gpg.ssh.program=ssh-keygen",
                    "-c", f"gpg.ssh.allowedSignersFile={name}", "verify-tag", ref], check=False)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)
    if r.returncode != 0:
        raise Untrusted(_reason(r.stderr))


def check(sh, install_dir: str, tag: str, tmp_dir: Path | None = None) -> None:
    """Callers use this through the module (`trust.check`) so tests can replace it."""
    verify_tag(sh, install_dir, tag, trusted_signers(sh, install_dir), tmp_dir)


def fingerprints(signers: str) -> list[str]:
    """`SHA256:` fingerprints of the keys in an allowed_signers text (as `ssh-keygen -l`)."""
    out = []
    for line in signers.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        toks = line.split()
        for i, t in enumerate(toks[:-1]):
            if t in _KEY_TYPES:
                try:
                    blob = base64.b64decode(toks[i + 1], validate=True)
                except ValueError:
                    break
                digest = hashlib.sha256(blob).digest()
                out.append("SHA256:" + base64.b64encode(digest).decode().rstrip("="))
                break
    return out

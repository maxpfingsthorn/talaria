"""The shipped release signing keys and their documented fingerprints (pure Python)."""
from __future__ import annotations

from pathlib import Path

from talaria import trust

ROOT = Path(__file__).resolve().parent.parent
WANT = {
    "SHA256:ob49+O53abdllIuiNpG41DSh+LillBnxCp5+WQM4vBQ",
    "SHA256:tbAvHjz+RsSsKDQqrUOQoA+VLfqvlFpuwjCbGOTwWQY",
    "SHA256:o/yXZPCUepVc67iVKUK0Rg2St70Bc4FvL9rZg/c8h7Q",
}


def test_shipped_keys_are_the_three_release_keys():
    fps = trust.fingerprints((ROOT / "keys" / "allowed_signers").read_text())
    assert len(fps) == 3 and set(fps) == WANT


def test_readme_and_security_doc_list_the_trusted_keys():
    for doc in ("README.md", "docs/security.md"):
        text = (ROOT / doc).read_text()
        for fp in WANT:
            assert fp in text, (doc, fp)

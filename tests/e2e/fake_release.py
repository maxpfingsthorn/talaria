# tests/e2e/fake_release.py
"""A static web server that looks like a GitHub repository with gbrain releases, for the
gbrain e2e test (plan ruling R1): the bare git repository over git's dumb HTTP protocol
(`git ls-remote`), each release's asset under releases/download/<tag>/, and its JSON with
the asset's sha256 digest (as GitHub's API has it) under api/releases/tags/<tag>."""
import functools
import hashlib
import json
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SRC = Path(__file__).parent / "fake_gbrain" / "fake_gbrain.c"
ASSET = "gbrain-linux-x64"


def run(*argv):
    subprocess.run([str(a) for a in argv], check=True, capture_output=True, text=True,
                   timeout=300)


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class FakeReleases:
    def __init__(self, root: Path, port: int = 8092):
        self.root, self.port = Path(root), port
        self.repo = self.root / "gbrain"
        self.work = self.root.parent / "gbrain-work"
        self.url = f"http://127.0.0.1:{port}/gbrain"
        self.root.mkdir(parents=True, exist_ok=True)
        run("git", "init", "-q", "--bare", self.repo)
        run("git", "clone", "-q", self.repo, self.work)
        run("git", "-C", self.work, "config", "user.email", "e2e@example.invalid")
        run("git", "-C", self.work, "config", "user.name", "e2e")
        handler = functools.partial(_Quiet, directory=str(self.root))
        self.server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, version: str, schema: int) -> str:
        tag = f"v{version}"
        dl = self.repo / "releases/download" / tag
        dl.mkdir(parents=True)
        binary = dl / ASSET
        run("gcc", "-O2", "-static", f'-DVERSION="{version}"', f"-DSCHEMA={schema}", "-o",
            binary, SRC)
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        api = self.repo / "api/releases/tags"
        api.mkdir(parents=True, exist_ok=True)
        (api / tag).write_text(json.dumps({"tag_name": tag, "assets": [
            {"name": ASSET, "digest": f"sha256:{digest}"}]}))
        run("git", "-C", self.work, "commit", "-q", "--allow-empty", "-m", tag)
        run("git", "-C", self.work, "tag", tag)
        run("git", "-C", self.work, "push", "-q", "origin", "HEAD", tag)
        run("git", "-C", self.repo, "update-server-info")
        run("chmod", "-R", "a+rX", self.root)
        return tag

    def shutdown(self):
        self.server.shutdown()

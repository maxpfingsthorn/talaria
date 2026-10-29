"""tools/verify-release-tag against real git repos and ssh-keygen; shape of the workflows."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "verify-release-tag"
GIT = ["git", "-c", "user.email=e2e@example.invalid", "-c", "user.name=e2e",
       "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def run(*argv, cwd=None):
    return subprocess.run([str(a) for a in argv], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout


def keygen(path: Path) -> str:
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", path)
    return Path(f"{path}.pub").read_text().strip()


def sign(repo: Path, key: Path, tag: str) -> None:
    run(*GIT, "-c", "gpg.format=ssh", "-c", f"user.signingkey={key}", "tag", "-s", tag, "-m", "x",
        cwd=repo)


def commit(repo: Path, version: str, signers: str | None = None) -> None:
    (repo / "talaria").mkdir(exist_ok=True)
    (repo / "talaria" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    if signers is not None:
        (repo / "keys").mkdir(exist_ok=True)
        (repo / "keys" / "allowed_signers").write_text(signers)
    run(*GIT, "add", "-A", cwd=repo)
    run(*GIT, "commit", "-q", "-m", version, cwd=repo)


@pytest.fixture
def repo(tmp_path):
    """A checkout whose `origin/main` is its first commit (version 1.2.3, one listed key)."""
    run("git", "init", "-q", "--bare", tmp_path / "origin.git")
    r = tmp_path / "repo"
    r.mkdir()
    run("git", "init", "-q", "-b", "work", cwd=r)
    run("git", "remote", "add", "origin", tmp_path / "origin.git", cwd=r)
    pub = keygen(tmp_path / "k1")
    commit(r, "1.2.3", f'e2e@example.invalid namespaces="git" {pub}\n')
    run(*GIT, "push", "-q", "origin", "HEAD:refs/heads/main", cwd=r)
    run(*GIT, "fetch", "-q", "origin", "main", cwd=r)
    return r


def verify(repo, tag):
    return subprocess.run(["bash", str(SCRIPT), tag], cwd=repo, capture_output=True, text=True)


def refused(repo, tag, reason):
    p = verify(repo, tag)
    assert p.returncode == 1, p.stdout + p.stderr
    assert f"release refused: {reason}" in p.stderr, p.stderr


@pytest.mark.real_trust
@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")
class TestVerifyReleaseTag:
    def test_signed_tag_on_main_passes(self, repo, tmp_path):
        sign(repo, tmp_path / "k1", "v1.2.3")
        p = verify(repo, "v1.2.3")
        assert p.returncode == 0, p.stderr

    def test_unsigned_annotated_tag_is_refused(self, repo):
        run(*GIT, "tag", "-a", "v1.2.3", "-m", "x", cwd=repo)
        refused(repo, "v1.2.3", "v1.2.3 is not signed by a key in its keys/allowed_signers")

    def test_lightweight_tag_is_refused(self, repo):
        run(*GIT, "tag", "v1.2.3", cwd=repo)
        refused(repo, "v1.2.3", "v1.2.3 is not an annotated, signed tag")

    def test_unlisted_key_is_refused(self, repo, tmp_path):
        keygen(tmp_path / "other")
        sign(repo, tmp_path / "other", "v1.2.3")
        refused(repo, "v1.2.3", "v1.2.3 is not signed by a key in its keys/allowed_signers")

    def test_tag_not_on_main_is_refused(self, repo, tmp_path):
        run(*GIT, "commit", "-q", "--allow-empty", "-m", "unmerged", cwd=repo)
        sign(repo, tmp_path / "k1", "v1.2.3")
        refused(repo, "v1.2.3", "v1.2.3 is not on origin/main")

    def test_version_mismatch_is_refused(self, repo, tmp_path):
        sign(repo, tmp_path / "k1", "v1.2.4")
        refused(repo, "v1.2.4",
                "v1.2.4 does not match the version in the tagged commit (1.2.3)")

    def test_commit_without_signers_file_is_refused(self, repo, tmp_path):
        run(*GIT, "rm", "-q", "-r", "keys", cwd=repo)
        run(*GIT, "commit", "-q", "-m", "no keys", cwd=repo)
        sign(repo, tmp_path / "k1", "v1.2.3")
        refused(repo, "v1.2.3", "v1.2.3 has no keys/allowed_signers")

    def test_commit_without_version_file_is_refused(self, repo, tmp_path):
        run(*GIT, "rm", "-q", "-r", "talaria", cwd=repo)
        run(*GIT, "commit", "-q", "-m", "no version", cwd=repo)
        run(*GIT, "push", "-q", "origin", "HEAD:refs/heads/main", cwd=repo)
        run(*GIT, "fetch", "-q", "origin", "main", cwd=repo)
        sign(repo, tmp_path / "k1", "v1.2.3")
        refused(repo, "v1.2.3", "v1.2.3 has no talaria/__init__.py")

    def test_version_is_read_from_the_tagged_commit(self, repo, tmp_path):
        sign(repo, tmp_path / "k1", "v1.2.3")
        (repo / "talaria" / "__init__.py").write_text('__version__ = "9.9.9"\n')
        assert verify(repo, "v1.2.3").returncode == 0


def load(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def test_release_has_no_expression_in_run():
    for job in load("release.yml")["jobs"].values():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), step


def test_release_publish_needs_verify_and_has_provenance_permissions():
    jobs = load("release.yml")["jobs"]
    assert jobs["publish"]["needs"] in ("verify", ["verify"])
    assert jobs["publish"]["permissions"] == {"contents": "write", "id-token": "write",
                                              "attestations": "write"}
    assert not {"ci", "mutation", "contract"} & set(jobs)


GATE_IF = ("github.event_name == 'workflow_dispatch' || "
           "(github.event_name == 'pull_request' && github.base_ref == 'main')")


def test_ci_gates_run_on_the_release_pr():
    jobs = load("ci.yml")["jobs"]
    for name in ("mutation", "contract"):
        assert " ".join(jobs[name]["if"].split()) == GATE_IF

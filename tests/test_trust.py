"""Release-tag signature check, against real git repos and real ssh-keygen."""
from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from talaria import trust
from talaria.shell import Result, Shell

pytestmark = [pytest.mark.real_trust,
              pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")]

DATA = Path(__file__).parent / "data"
GIT = ["git", "-c", "user.email=e2e@example.invalid", "-c", "user.name=e2e",
       "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch):
    """The developer's own git config (gpg settings) must not leak in."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def run(*argv, cwd=None):
    return subprocess.run([str(a) for a in argv], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout


def keygen(path: Path) -> str:
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", path)
    return Path(f"{path}.pub").read_text().strip()


def signer_line(pub: str, extra: str = "") -> str:
    return f'e2e@example.invalid namespaces="git"{extra} {pub}\n'


def sign(repo: Path, key: Path, tag: str) -> None:
    run(*GIT, "-c", "gpg.format=ssh", "-c", f"user.signingkey={key}", "tag", "-s", tag, "-m", "x",
        cwd=repo)


def commit_signers(repo: Path, text: str, msg: str) -> None:
    (repo / "keys").mkdir(exist_ok=True)
    (repo / "keys" / "allowed_signers").write_text(text)
    run(*GIT, "add", "-A", cwd=repo)
    run(*GIT, "commit", "-q", "-m", msg, cwd=repo)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    run("git", "init", "-q", cwd=r)
    return r


def setup_signed(repo, tmp_path, extra=""):
    key = tmp_path / "k1"
    pub = keygen(key)
    commit_signers(repo, signer_line(pub, extra), "A")
    return key, pub


def test_listed_key_is_accepted(repo, tmp_path):
    key, _ = setup_signed(repo, tmp_path)
    sign(repo, key, "v1")
    trust.check(Shell(), str(repo), "v1")


def reason(repo, tag):
    with pytest.raises(trust.Untrusted) as e:
        trust.check(Shell(), str(repo), tag)
    return str(e.value)


def test_unsigned_annotated_tag_is_refused(repo, tmp_path):
    setup_signed(repo, tmp_path)
    run(*GIT, "tag", "-a", "v1", "-m", "x", cwd=repo)
    assert reason(repo, "v1") != "v1 is a lightweight tag"


def test_lightweight_tag_is_refused(repo, tmp_path):
    setup_signed(repo, tmp_path)
    run(*GIT, "tag", "v1", cwd=repo)
    assert "lightweight" in reason(repo, "v1")


def test_unlisted_key_is_refused(repo, tmp_path):
    setup_signed(repo, tmp_path)
    other = tmp_path / "other"
    keygen(other)
    sign(repo, other, "v1")
    assert "lightweight" not in reason(repo, "v1")


def test_expired_key_is_refused(repo, tmp_path):
    key, _ = setup_signed(repo, tmp_path, extra=' valid-before="20200101"')
    sign(repo, key, "v1")
    assert "lightweight" not in reason(repo, "v1")


def test_tag_adding_its_own_key_is_refused(repo, tmp_path):
    key1, pub1 = setup_signed(repo, tmp_path)
    key2 = tmp_path / "k2"
    pub2 = keygen(key2)
    run(*GIT, "tag", "base", cwd=repo)
    commit_signers(repo, signer_line(pub1) + signer_line(pub2), "B")
    sign(repo, key2, "v1")
    run(*GIT, "checkout", "-q", "base", cwd=repo)   # the install's HEAD stays at A
    assert reason(repo, "v1")


def test_missing_signers_file(repo):
    run(*GIT, "commit", "-q", "--allow-empty", "-m", "A", cwd=repo)
    with pytest.raises(trust.Untrusted) as e:
        trust.trusted_signers(Shell(), str(repo))
    assert str(e.value) == "this install has no trusted release keys"


def test_blank_signers_file(repo):
    commit_signers(repo, "\n", "A")
    with pytest.raises(trust.Untrusted, match="no trusted release keys"):
        trust.trusted_signers(Shell(), str(repo))


@pytest.mark.parametrize("ok", [True, False], ids=["success", "failure"])
def test_temp_file_removed(repo, tmp_path, ok):
    key, _ = setup_signed(repo, tmp_path)
    if ok:
        sign(repo, key, "v1")
    else:
        run(*GIT, "tag", "-a", "v1", "-m", "x", cwd=repo)
    t = tmp_path / "t"
    t.mkdir()
    if ok:
        trust.check(Shell(), str(repo), "v1", tmp_dir=t)
    else:
        with pytest.raises(trust.Untrusted):
            trust.check(Shell(), str(repo), "v1", tmp_dir=t)
    assert list(t.iterdir()) == []


def test_temp_file_is_private(tmp_path):
    seen = []

    class Sh:
        def run(self, argv, **kw):
            if argv[3] == "cat-file":
                return Result(0, "tag\n")
            f = Path([a for a in argv if str(a).startswith("gpg.ssh.allowedSignersFile=")][0].split("=", 1)[1])
            seen.append(f.stat().st_mode & 0o777)
            return Result(0)

    trust.verify_tag(Sh(), "/x", "v1", "sig", tmp_dir=tmp_path)
    assert seen == [0o600]


def test_reason_is_one_short_line():
    class Sh:
        def run(self, argv, **kw):
            if argv[3] == "cat-file":
                return Result(0, "tag\n")
            return Result(1, "", "a\n" + "x" * 500 + "\n")

    with pytest.raises(trust.Untrusted) as e:
        trust.verify_tag(Sh(), "/x", "v1", "sig")
    assert "\n" not in str(e.value) and 0 < len(str(e.value)) <= 200


def test_reason_default_when_stderr_empty():
    class Sh:
        def run(self, argv, **kw):
            if argv[3] == "cat-file":
                return Result(0, "tag\n")
            return Result(1, "", "\n  \n")

    with pytest.raises(trust.Untrusted, match="^signature check failed$"):
        trust.verify_tag(Sh(), "/x", "v1", "sig")


def test_hardware_key_signature_fixture(tmp_path):
    clone = tmp_path / "clone"
    run("git", "clone", "-q", DATA / "sk-signed-tag.bundle", clone)
    run("git", "-C", clone, "fetch", "-q", "origin", "+refs/tags/v0.0.1:refs/tags/v0.0.1")
    trust.check(Shell(), str(clone), "v0.0.1")
    fresh = keygen(tmp_path / "fresh")
    with pytest.raises(trust.Untrusted):
        trust.verify_tag(Shell(), str(clone), "v0.0.1", signer_line(fresh))


def test_fingerprints(tmp_path):
    pub = keygen(tmp_path / "k")
    want = run("ssh-keygen", "-lf", tmp_path / "k.pub").split()[1]
    text = "# comment\n\n" + signer_line(pub) + "   \n# more\n"
    assert trust.fingerprints(text) == [want]
    blob = pub.split()[1]
    assert want == "SHA256:" + base64.b64encode(
        hashlib.sha256(base64.b64decode(blob)).digest()).decode().rstrip("=")

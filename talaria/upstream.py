from __future__ import annotations

from talaria.tags import SEMVER, is_release, semver_newer


def _ls_remote(sh, repo: str) -> dict[str, str]:
    out: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in sh.run(["git", "ls-remote", "--tags", repo], timeout=120).stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if not ref.startswith("refs/tags/"):
            continue
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):
            peeled[name[:-3]] = sha
        else:
            out[name] = sha
    out.update(peeled)
    return out


def git_release_tags(sh, repo: str) -> dict[str, str]:
    return {t: c for t, c in _ls_remote(sh, repo).items() if is_release(t)}


def registry_tags(sh, image: str, tls_verify: bool = True) -> set[str]:
    argv = ["podman", "search", "--list-tags", "--limit", "1000"]
    if not tls_verify:
        argv.append("--tls-verify=false")
    argv.append(image)
    lines = sh.run(argv, timeout=120).stdout.splitlines()[1:]
    return {l.split()[-1] for l in lines if l.split()}


def latest_semver(sh, repo: str) -> str | None:
    best = None
    for t in _ls_remote(sh, repo):
        if SEMVER.match(t) and (best is None or semver_newer(t, best)):
            best = t
    return best

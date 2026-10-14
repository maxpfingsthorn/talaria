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


# podman fetches every tag and then truncates to --limit
TAG_LIMIT = 100000


def registry_tags(sh, image: str, tls_verify: bool = True) -> set[str]:
    argv = ["podman", "search", "--list-tags", "--limit", str(TAG_LIMIT)]
    if not tls_verify:
        argv.append("--tls-verify=false")
    argv.append(image)
    lines = sh.run(argv, timeout=120).stdout.splitlines()[1:]
    found = {ln.split()[-1] for ln in lines if ln.split()}
    if len(lines) >= TAG_LIMIT:
        raise ValueError(f"{image}: {TAG_LIMIT} or more tags; the list may be cut")
    return found


def latest_semver(sh, repo: str) -> str | None:
    best = None
    for t in _ls_remote(sh, repo):
        if SEMVER.match(t) and (best is None or semver_newer(t, best)):
            best = t
    return best

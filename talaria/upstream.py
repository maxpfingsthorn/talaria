from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.request

from talaria.tags import SEMVER, is_release


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
    """The latest published stable Talaria release; a tag alone has not passed CI.

    `sh` stays in the interface for callers from earlier releases. Non-GitHub HTTP
    repositories can provide the same JSON at <repo>/api/releases/latest.
    """
    gh = re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?", repo)
    if gh:
        url = f"https://api.github.com/repos/{gh[1]}/releases/latest"
    elif repo.startswith(("https://", "http://")):
        url = f"{repo.rstrip('/')}/api/releases/latest"
    else:
        raise ValueError("release discovery needs an HTTP repository with a releases API")
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "talaria"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:      # GitHub returns 404 when no stable release is published
            return None
        raise
    except http.client.HTTPException as e:   # a cut or malformed response is no OSError
        raise OSError(f"release lookup failed: {type(e).__name__}") from e
    if len(body) > 1024 * 1024:
        raise ValueError("release metadata is too large")
    record = json.loads(body)
    if not isinstance(record, dict):
        raise ValueError("release metadata is not an object")
    tag = record.get("tag_name")
    if (isinstance(tag, str) and SEMVER.fullmatch(tag)
            and record.get("draft") is False and record.get("prerelease") is False
            and record.get("published_at")):
        return tag
    return None

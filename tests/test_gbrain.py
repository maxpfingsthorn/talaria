# tests/test_gbrain.py
import hashlib
import json
from pathlib import Path

import pytest

from talaria import apps
from talaria.apps import gbrain as gb
from talaria.conf import load_conf
from talaria.ctx import Paths, TooLarge
from talaria.images import RevisionMismatch
from talaria.rehearse import Transient
from talaria.shell import Result
from tests.fakes import make_test_ctx

BIN = b"\x7fELF fake gbrain"
SUM = hashlib.sha256(BIN).hexdigest()
TAG = "v0.60.116.0"
GH = "https://github.com/garrytan/gbrain"


def release_json(digest=f"sha256:{SUM}", name="gbrain-linux-x64"):
    return json.dumps({"tag_name": TAG, "assets": [
        {"name": "gbrain-darwin-arm64", "digest": "sha256:" + "0" * 64},
        {"name": name, "digest": digest}]}).encode()


def gctx(tmp_path, monkeypatch, api=None, files=None, gh=None,
         version="gbrain 0.60.116.0\n"):
    ctx = make_test_ctx(tmp_path, app="gbrain")
    seen = {"get": [], "download": [], "containerfile": []}
    status, body = api or (200, release_json())

    def http_get(url, timeout=5.0):
        seen["get"].append((url, timeout))
        return status, body

    def download(url, dest, max_bytes):
        seen["download"].append((url, max_bytes))
        data = (files if files is not None else {"gbrain-linux-x64": BIN}).get(url.rsplit("/", 1)[1])
        if data is None:
            return 404
        dest.write_bytes(data)
        return 200

    def build(argv, input):
        seen["containerfile"].append((Path(argv[-1]) / "Containerfile").read_text())
        return Result(0, "sha256:built\n")
    ctx.http_get, ctx.download, ctx.seen = http_get, download, seen
    monkeypatch.setattr(gb, "which", lambda t: gh)
    ctx.sh.on("uname", "-m", out="x86_64\n")
    ctx.sh.on("podman", "build", fn=build)
    ctx.sh.on("podman", "run", out=version)
    return ctx


def test_registry_and_attributes():
    assert apps.NAMES == ("hermes", "clawvisor", "gbrain")
    a = apps.get("gbrain")
    assert (a.name, a.title, a.unit, a.container, a.quadlet_file, a.env_file, a.local_image) == (
        "gbrain", "gbrain", "gbrain.service", "gbrain", "gbrain.container", "gbrain.env",
        "localhost/gbrain")
    assert (a.default_data_dir, a.default_port, a.container_port, a.default_repo,
            a.min_release, a.backup_exclude, a.can_adopt) == (
        "~/gbrain-data", 3131, 3131, GH, "v0.60.116.0", (), False)
    assert (a.has_maintenance, a.copy_stopped, a.default_check_days,
            a.default_maintenance_time, a.prepare_summary, a.before_start_error) == (
        True, True, ("mon", "thu"), "03:30", "admin token", "the brain check before start failed")


def test_conf_defaults(tmp_path):
    c = load_conf(Paths(tmp_path, "gbrain"))
    assert (c.data_dir, c.dashboard_port, c.repo, c.min_release, c.check_days,
            c.maintenance_time, c.backup_exclude) == (
        tmp_path / "gbrain-data", 3131, GH, "v0.60.116.0", ("mon", "thu"), "03:30", ())


def test_release_tags_have_four_numbers():
    a = apps.get("gbrain")
    assert a.is_release("v0.60.116.0")
    assert not any(a.is_release(t) for t in ("v0.60.116", "v0.60.116.0-rc1", "latest", "0.60.1.0"))
    assert a.tag_key("v0.60.116.0") == (0, 60, 116, 0)
    assert a.tag_key("v0.60.9.0") < a.tag_key("v0.60.116.0") < a.tag_key("v0.61.1.0")
    with pytest.raises(ValueError, match="not a release tag: 'latest'"):
        a.tag_key("latest")


def test_releases_keep_only_release_tags(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path, app="gbrain")
    seen = {}

    def ls_remote(sh, repo):
        seen["args"] = (sh, repo)
        return {"v0.60.116.0": "a", "v0.60.117.0-rc1": "b", "latest": "c", "v0.60.9.0": "d"}
    monkeypatch.setattr(gb, "_ls_remote", ls_remote)
    assert ctx.app.releases(ctx) == {"v0.60.116.0": "a", "v0.60.9.0": "d"}
    assert seen["args"] == (ctx.sh, GH)
    assert ctx.app.published(ctx, ["v1", "v2"]) == {"v1", "v2"}
    assert ctx.app.image_refs(ctx) == ["localhost/gbrain"]


def test_github_repo_and_release_api():
    assert gb.github_repo(GH) == "garrytan/gbrain"
    assert gb.github_repo(GH + ".git") == "garrytan/gbrain"
    assert gb.github_repo(GH + "/") == "garrytan/gbrain"
    assert gb.github_repo("http://127.0.0.1:8092/gbrain") is None
    assert gb.release_api(GH, TAG) == f"https://api.github.com/repos/garrytan/gbrain/releases/tags/{TAG}"
    assert gb.release_api("http://127.0.0.1:8092/gbrain", TAG) == \
        f"http://127.0.0.1:8092/gbrain/api/releases/tags/{TAG}"


def test_base_is_pinned_by_digest():
    assert __import__("re").fullmatch(r"gcr\.io/distroless/cc-debian12@sha256:[0-9a-f]{64}", gb.BASE)


def test_fetch_verifies_the_digest_and_builds(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    rec = ctx.app.fetch(ctx, TAG, "abc")
    assert rec == {"tag": TAG, "id": "sha256:built", "digest": f"sha256:{SUM}",
                   "ref": f"build:{TAG}", "commit": "abc"}
    assert ctx.seen["get"] == [(f"https://api.github.com/repos/garrytan/gbrain/releases/tags/{TAG}", 30.0)]
    assert ctx.seen["download"] == [(f"{GH}/releases/download/{TAG}/gbrain-linux-x64", gb.MAX_BINARY)]
    (build,) = ctx.sh.called("podman", "build")
    assert build[:-1] == ["podman", "build", "-q", "--pull=missing", "--timestamp", "0",
                          "--label", "org.opencontainers.image.revision=abc",
                          "--label", f"org.opencontainers.image.version={TAG}",
                          "-t", f"localhost/gbrain:{TAG}"]
    (cf,) = ctx.seen["containerfile"]
    assert cf == (f"FROM {gb.BASE}\nCOPY --chmod=0755 gbrain /usr/local/bin/gbrain\n"
                  "EXPOSE 3131\nUSER 65532:65532\nENTRYPOINT [\"/usr/local/bin/gbrain\"]\n")
    assert ctx.sh.called("podman", "run") == [["podman", "run", "--rm", "--network=none",
                                               "sha256:built", "--version"]]
    assert not (ctx.paths.staging / f"build-{TAG}").exists()


def test_digest_mismatch_fails_verification(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, files={"gbrain-linux-x64": b"tampered"})
    with pytest.raises(RevisionMismatch, match=rf"{TAG}: gbrain-linux-x64 failed verification \(sha256"):
        ctx.app.fetch(ctx, TAG, "abc")
    assert ctx.sh.called("podman", "build") == []
    assert not (ctx.paths.staging / f"build-{TAG}").exists()


@pytest.mark.parametrize("digest", ["", "md5:abc", "sha256:XYZ"])
def test_a_missing_digest_fails_verification(tmp_path, monkeypatch, digest):
    ctx = gctx(tmp_path, monkeypatch, api=(200, release_json(digest=digest)))
    with pytest.raises(RevisionMismatch, match="publishes no sha256 digest"):
        ctx.app.fetch(ctx, TAG, "abc")


@pytest.mark.parametrize("api,files,msg", [
    ((404, b""), None, "not published yet \\(release API answered 404\\)"),
    ((0, b""), None, "not published yet \\(release API answered nothing\\)"),
    ((200, b"<html>"), None, "answered no JSON"),
    ((200, release_json(name="gbrain-linux-arm64")), None, "release assets of v0.60.116.0 are not published yet"),
    (None, {}, "release assets of v0.60.116.0 are not published yet"),
])
def test_unpublished_or_unreachable_release_is_transient(tmp_path, monkeypatch, api, files, msg):
    ctx = gctx(tmp_path, monkeypatch, api=api, files=files)
    with pytest.raises(Transient, match=msg):
        ctx.app.fetch(ctx, TAG, "abc")


def test_rate_limited_release_api_is_transient(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, api=(403, b'{"message": "API rate limit exceeded"}'))
    with pytest.raises(Transient, match="answered 403"):
        ctx.app.fetch(ctx, TAG, "abc")


def test_oversized_asset_is_permanent(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)

    def big(url, dest, max_bytes):
        raise TooLarge("too big")
    ctx.download = big
    with pytest.raises(RevisionMismatch, match="download limit"):
        ctx.app.fetch(ctx, TAG, "abc")


def test_unsupported_architecture(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    ctx.sh.on("uname", "-m", out="aarch64\n")
    with pytest.raises(RevisionMismatch, match="unsupported architecture: aarch64"):
        ctx.app.fetch(ctx, TAG, "abc")
    assert ctx.seen["get"] == []


@pytest.mark.parametrize("out,ok", [
    ("gbrain 0.60.116.0\n", True),
    ("UPGRADE_AVAILABLE 0.60.116.0 0.60.120.0\nRun: gbrain self-upgrade\ngbrain 0.60.116.0\n", True),
    ("gbrain 0.60.115.0\n", False),
    ("", False),
])
def test_the_binary_must_report_the_tag(tmp_path, monkeypatch, out, ok):
    ctx = gctx(tmp_path, monkeypatch, version=out)
    if ok:
        assert ctx.app.fetch(ctx, TAG, "abc")["tag"] == TAG
    else:
        with pytest.raises(RevisionMismatch, match="the binary reports version"):
            ctx.app.fetch(ctx, TAG, "abc")


def test_attestation_runs_when_gh_is_logged_in(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, gh="/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status").on("gh", "attestation", "verify")
    ctx.app.fetch(ctx, TAG, "abc")
    (v,) = ctx.sh.called("gh", "attestation")
    assert v[:3] == ["gh", "attestation", "verify"] and v[4:] == ["-R", "garrytan/gbrain"]
    assert v[3].endswith(f"build-{TAG}/gbrain")
    assert ctx.sh.timeouts[ctx.sh.calls.index(v)] == 300


def test_attestation_failure_is_permanent(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, gh="/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status")
    ctx.sh.on("gh", "attestation", rc=1, err="Verification failed\nno matching attestations\n")
    with pytest.raises(RevisionMismatch, match=r"failed verification \(attestation: no matching attestations\)"):
        ctx.app.fetch(ctx, TAG, "abc")
    assert ctx.sh.called("podman", "build") == []


def test_attestation_skipped_without_a_gh_login(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, gh="/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status", rc=1)
    ctx.app.fetch(ctx, TAG, "abc")
    assert ctx.sh.called("gh", "attestation") == []


def test_attestation_unavailable_reasons(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, gh=None)
    assert gb.attestation_unavailable(ctx) == "gh is not installed"
    monkeypatch.setattr(gb, "which", lambda t: "/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status", rc=1)
    assert gb.attestation_unavailable(ctx) == "gh is not logged in for this account"
    ctx.sh.on("gh", "auth", "status", rc=0)
    assert gb.attestation_unavailable(ctx) is None
    ctx.conf.repo = "http://127.0.0.1:8092/gbrain"
    assert gb.attestation_unavailable(ctx) == "the repository is not on GitHub"


def test_reacquire_fetches_again(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    ctx.app.reacquire(ctx, {"tag": TAG, "id": "sha256:old", "commit": "abc"})
    assert len(ctx.sh.called("podman", "build")) == 1


@pytest.mark.parametrize("answer,reason", [
    ((200, b'{"status":"ok","version":"0.60.116.0","engine":"pglite"}'), None),
    ((503, b'{"status":"unavailable"}'), "/health answered 503"),
    ((0, b""), "/health answered nothing"),
    ((200, b"<html>"), "/health did not answer JSON"),
    ((200, b'{"status":"degraded"}'), "/health reports status degraded"),
    ((200, b"[]"), "/health reports status ?"),
])
def test_health(tmp_path, answer, reason):
    ctx = make_test_ctx(tmp_path, app="gbrain")
    urls = []
    ctx.http_get = lambda url, timeout=5.0: (urls.append((url, timeout)), answer)[1]
    assert ctx.app.health(ctx) == reason
    assert urls == [("http://127.0.0.1:3131/health", 5.0)]


import os
import subprocess

from talaria import rehearse, setup, units
from talaria.conf import parse_kv


def doctor(v, status="ok"):
    msg = (f"Version {v} (latest: {v})" if status == "ok"
           else f"Version {v} is AHEAD of this client's latest known version (219).")
    return json.dumps({"status": status, "checks": [
        {"name": "embeddings", "status": "warn", "message": "disabled"},
        {"name": "schema_version", "status": status, "message": msg}]}) + "\n"


def split_run(argv):
    """(image, gbrain args) of a oneoff `podman run`."""
    i = argv.index("GBRAIN_HOME=/data") + 1
    if argv[i] == "--env-file":
        i += 2
    return argv[i], argv[i + 1:]


URL = "https://brain.example.ts.net:8443"


def brain(tmp_path, answers=None):
    ctx = make_test_ctx(tmp_path, app="gbrain", dashboard_public_url=URL)
    runs = []
    answers = {"doctor": Result(0, doctor(221)), "recall": Result(0, f"{gb.MARKER_TEXT}\n"),
               **(answers or {})}

    def fn(argv, input):
        image, args = split_run(argv)
        runs.append((image, args, argv))
        a = answers.get(args[0], Result(0, ""))
        return a(argv) if callable(a) else a
    ctx.sh.on("podman", "rm")
    ctx.sh.on("podman", "run", fn=fn)
    ctx.runs = runs
    return ctx


def raises(exc):
    def f(argv):
        raise exc
    return f


# --- parse_schema, oneoff -------------------------------------------------------

def test_parse_schema_reads_the_schema_check():
    assert gb.parse_schema(doctor(221)) == 221
    assert gb.parse_schema("UPGRADE_AVAILABLE 1 2\n" + doctor(7)) == 7


@pytest.mark.parametrize("out,msg", [
    (doctor(221, "warn"), "schema_version is warn: Version 221 is AHEAD"),
    ("", "doctor printed no JSON"),
    ("{nope}", "doctor printed no valid JSON"),
    (json.dumps({"checks": []}), "doctor reports no schema_version check"),
    (json.dumps({"checks": [{"name": "schema_version", "status": "ok", "message": "fine"}]}),
     "schema_version names no version"),
    ("[1]", "doctor reports no schema_version check"),
])
def test_parse_schema_refuses(out, msg):
    with pytest.raises(ValueError, match=msg):
        gb.parse_schema(out)


def test_oneoff_is_offline_without_keys_by_default(tmp_path):
    ctx = brain(tmp_path)
    gb.oneoff(ctx, "img", tmp_path / "d", ["doctor", "--json"])
    rm = ["podman", "rm", "-f", "talaria-gbrain-oneoff"]
    assert ctx.sh.calls == [rm, ["podman", "run", "--rm", "--name", "talaria-gbrain-oneoff",
                                 "--read-only", "--userns=keep-id:uid=65532,gid=65532",
                                 "--network=none", "-v", f"{tmp_path / 'd'}:/data:Z",
                                 "-e", "HOME=/data", "-e", "GBRAIN_HOME=/data", "img",
                                 "doctor", "--json"], rm]
    assert ctx.sh.timeouts == [120, 300, 120]


def test_oneoff_with_network_and_env(tmp_path):
    ctx = brain(tmp_path)
    gb.oneoff(ctx, "img", tmp_path, ["dream"], network=True, env=True, timeout=3600)
    run = ctx.runs[0][2]
    assert "--network=none" not in run
    assert run[run.index("--env-file") + 1] == str(ctx.paths.app_env)
    assert ctx.sh.timeouts[1] == 3600


def test_oneoff_removes_its_container_after_a_timeout(tmp_path):
    ctx = brain(tmp_path, {"dream": raises(subprocess.TimeoutExpired(["podman"], 3600))})
    with pytest.raises(subprocess.TimeoutExpired):
        gb.oneoff(ctx, "img", tmp_path, ["dream"])
    assert ctx.sh.calls[-1] == ["podman", "rm", "-f", "talaria-gbrain-oneoff"]


# --- Quadlet ---------------------------------------------------------------------

def test_quadlet(tmp_path):
    ctx = make_test_ctx(tmp_path, app="gbrain", dashboard_public_url=URL)
    q = units.render_quadlet(ctx)
    assert (f"\nExec=serve --http --bind 0.0.0.0 --port 3131 --public-url {URL} "
            "--enable-dcr --fail-fast --surface full\n") in q
    for line in ("ContainerName=gbrain", "Image=localhost/gbrain:current", "Pull=never",
                 "ReadOnly=true", "UserNS=keep-id:uid=65532,gid=65532",
                 f"Volume={ctx.conf.data_dir}:/data:Z", "Environment=HOME=/data GBRAIN_HOME=/data",
                 f"EnvironmentFile={ctx.paths.app_env}", "PublishPort=127.0.0.1:3131:3131",
                 f"ExecCondition=/bin/sh -c 'test ! -e \"{ctx.paths.marker}\"'"):
        assert f"\n{line}\n" in q, line
    assert q.startswith("# Managed by Talaria.") and "PUBLIC_URL" not in q and "${" not in q


def test_quadlet_needs_the_public_url(tmp_path):
    ctx = make_test_ctx(tmp_path, app="gbrain")
    with pytest.raises(ValueError, match="gbrain needs dashboard.public_url"):
        units.render_quadlet(ctx)


# --- prepare, notes, initial conf, ready text --------------------------------------

def test_prepare_generates_the_admin_token_once(tmp_path):
    ctx = brain(tmp_path)
    lines = ctx.app.prepare(ctx)
    token = parse_kv(ctx.paths.app_env.read_text())["GBRAIN_ADMIN_BOOTSTRAP_TOKEN"]
    assert len(token) >= 40 and token not in "".join(lines)
    assert lines == [f"admin token generated in {ctx.paths.app_env} (GBRAIN_ADMIN_BOOTSTRAP_TOKEN)"]
    assert (ctx.paths.app_env.stat().st_mode & 0o777) == 0o600
    assert (ctx.conf.data_dir.stat().st_mode & 0o777) == 0o700
    assert ctx.app.prepare(ctx) == []
    assert parse_kv(ctx.paths.app_env.read_text())["GBRAIN_ADMIN_BOOTSTRAP_TOKEN"] == token


def test_prepare_keeps_provider_keys(tmp_path):
    ctx = brain(tmp_path)
    ctx.paths.app_env.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.app_env.write_text("OPENAI_API_KEY=sk-x\n")
    ctx.app.prepare(ctx)
    env = parse_kv(ctx.paths.app_env.read_text())
    assert env["OPENAI_API_KEY"] == "sk-x" and env["GBRAIN_ADMIN_BOOTSTRAP_TOKEN"]


def test_prepare_stops_without_the_public_url(tmp_path):
    ctx = make_test_ctx(tmp_path, app="gbrain")
    with pytest.raises(ValueError, match="gbrain needs dashboard.public_url"):
        ctx.app.prepare(ctx)
    assert not ctx.paths.app_env.exists()


def test_setup_notes(tmp_path, monkeypatch):
    ctx = brain(tmp_path)
    monkeypatch.setattr(gb, "which", lambda t: None)
    assert ctx.app.setup_notes(ctx) == [
        "gbrain downloads are checked against the release's sha256 digest only (gh is not "
        "installed); install gh and run `gh auth login` as this account to also verify their "
        "build provenance"]
    ctx.conf.repo = "http://127.0.0.1:8092/gbrain"
    assert ctx.app.setup_notes(ctx) == []
    ctx.conf.repo = GH
    monkeypatch.setattr(gb, "which", lambda t: "/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status")
    assert ctx.app.setup_notes(ctx) == []


def test_initial_conf_and_ready_text(tmp_path):
    ctx = brain(tmp_path)
    assert ctx.app.initial_conf(ctx) == (
        "# Talaria settings; see README.\napp = gbrain\ndata_dir = ~/gbrain-data\n"
        "# required: the https address MCP connectors use, e.g.\n"
        "# dashboard.public_url = https://<host>.<tailnet>.ts.net:8443\n")
    assert ctx.app.ready_text(ctx) == (
        f"gbrain is running at http://127.0.0.1:3131 (admin UI /admin; its token is "
        f"GBRAIN_ADMIN_BOOTSTRAP_TOKEN in {ctx.paths.app_env}). MCP connectors use {URL}/mcp "
        "once you publish it (README: gbrain)")


# --- initialize, data_version ------------------------------------------------------

def test_initialize_a_fresh_brain(tmp_path):
    ctx = brain(tmp_path)
    data = ctx.conf.data_dir
    assert ctx.app.initialize(ctx) == [f"gbrain brain initialized in {data} (PGLite, schema 221)"]
    assert [args for _, args, _ in ctx.runs] == [
        ["init", "--pglite"], ["config", "set", "self_upgrade.mode", "off"],
        ["remember", gb.MARKER_TEXT, "--provenance", "talaria setup"], ["doctor", "--json"]]
    assert {image for image, _, _ in ctx.runs} == {"localhost/gbrain:current"}
    assert all("--network=none" in argv and "--env-file" not in argv for _, _, argv in ctx.runs)
    assert (data / ".talaria-schema").read_text() == "221\n"
    assert (data / ".talaria-init").exists()
    assert gb.MARKER_TEXT.startswith(gb.MARKER)
    ctx.runs.clear()
    assert ctx.app.initialize(ctx) == [] and ctx.runs == []


def test_initialize_skips_init_for_an_existing_brain(tmp_path):
    ctx = brain(tmp_path)
    (ctx.conf.data_dir / ".gbrain").mkdir()
    (ctx.conf.data_dir / ".gbrain/config.json").write_text("{}")
    ctx.app.initialize(ctx)
    assert [args[0] for _, args, _ in ctx.runs] == ["config", "remember", "doctor"]


def test_initialize_failure_stops_and_retries_next_time(tmp_path):
    ctx = brain(tmp_path, {"init": Result(1, "", "Error: cannot create /data/.gbrain\n")})
    with pytest.raises(ValueError, match="gbrain init failed: Error: cannot create /data/.gbrain"):
        ctx.app.initialize(ctx)
    assert not (ctx.conf.data_dir / ".talaria-init").exists()
    ctx = brain(tmp_path, {"doctor": Result(0, doctor(221, "warn"))})
    with pytest.raises(ValueError, match="gbrain doctor failed after init: schema_version is warn"):
        ctx.app.initialize(ctx)
    assert not (ctx.conf.data_dir / ".talaria-init").exists()


def test_data_version(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    assert gb.APP.data_version(d) is None
    (d / ".talaria-schema").write_text("221\n")
    assert gb.APP.data_version(d) == 221
    (d / ".talaria-schema").write_text("x\n")
    assert gb.APP.data_version(d) is None
    (d / ".talaria-schema").unlink()
    (tmp_path / "elsewhere").write_text("5\n")
    os.symlink(tmp_path / "elsewhere", d / ".talaria-schema")
    assert gb.APP.data_version(d) is None


# --- rehearsal ---------------------------------------------------------------------

IMG = {"tag": "v0.60.117.0", "id": "sha256:new", "digest": "sha256:dd"}


def copy_dir(tmp_path, schema="219\n"):
    c = tmp_path / "copy"
    c.mkdir()
    if schema:
        (c / ".talaria-schema").write_text(schema)
    return c


def test_rehearsal_reports_the_schema_change(tmp_path):
    ctx = brain(tmp_path)
    c = copy_dir(tmp_path)
    report = ctx.app.rehearse(ctx, {}, IMG, c, tmp_path)
    assert report == {"tag": "v0.60.117.0", "digest": "sha256:dd", "before": 219, "after": 221}
    assert [(image, args) for image, args, _ in ctx.runs] == [
        ("sha256:new", ["doctor", "--json"]), ("sha256:new", ["recall", "--query", gb.MARKER])]
    assert ctx.app.report_lines(ctx, report) == (["Brain schema: 219 → 221"], [])
    assert ctx.app.pending_extra(report) == {"schema_after": 221}


def test_rehearsal_runs_offline_without_the_env_file(tmp_path):
    ctx = brain(tmp_path)
    ctx.paths.app_env.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.app_env.write_text("OPENAI_API_KEY=sk-secret\n")
    ctx.app.rehearse(ctx, {}, IMG, copy_dir(tmp_path), tmp_path)
    for _, _, argv in ctx.runs:
        assert "--network=none" in argv and "--env-file" not in argv
        assert f"{tmp_path / 'copy'}:/data:Z" in argv


def test_rehearsal_without_a_schema_file_says_unknown(tmp_path):
    ctx = brain(tmp_path)
    report = ctx.app.rehearse(ctx, {}, IMG, copy_dir(tmp_path, schema=None), tmp_path)
    assert report["before"] is None
    assert ctx.app.report_lines(ctx, report) == (["Brain schema: unknown → 221"], [])


def test_rehearsal_doctor_not_ok_is_permanent(tmp_path):
    ctx = brain(tmp_path, {"doctor": Result(0, doctor(221, "warn"), "")})
    with pytest.raises(rehearse.Permanent, match="gbrain doctor on the copy is not ok: schema_version is warn") as e:
        ctx.app.rehearse(ctx, {}, IMG, copy_dir(tmp_path), tmp_path)
    assert e.value.details[0][0] == "gbrain doctor (last lines)"


@pytest.mark.parametrize("answer", [Result(0, "nothing found\n"), Result(1, "", "pglite_busy\n")])
def test_rehearsal_needs_the_marker_recalled(tmp_path, answer):
    ctx = brain(tmp_path, {"recall": answer})
    with pytest.raises(rehearse.Permanent, match="could not recall Talaria's marker page on the copy") as e:
        ctx.app.rehearse(ctx, {}, IMG, copy_dir(tmp_path), tmp_path)
    assert e.value.details[0][0] == "gbrain recall (last lines)"


def test_rehearsal_timeout_is_permanent(tmp_path):
    ctx = brain(tmp_path, {"doctor": raises(subprocess.TimeoutExpired(["podman"], 300))})
    with pytest.raises(rehearse.Permanent, match="did not finish its checks on the copy within 300 s"):
        ctx.app.rehearse(ctx, {}, IMG, copy_dir(tmp_path), tmp_path)


# --- deploy: before_start ------------------------------------------------------------

PENDING = {"tag": "v0.60.117.0", "image": {"id": "sha256:new"}, "schema_after": 221}


def test_before_start_reads_the_schema_with_the_new_image_on_stopped_data(tmp_path):
    ctx = brain(tmp_path)
    assert ctx.app.before_start(ctx, PENDING) == (None, [])
    ((image, args, argv),) = ctx.runs
    assert (image, args) == ("sha256:new", ["doctor", "--json"])
    assert f"{ctx.conf.data_dir}:/data:Z" in argv and "--network=none" in argv
    assert ctx.app.data_version(ctx.conf.data_dir) == 221


def test_before_start_schema_mismatch_fails_the_deploy(tmp_path):
    ctx = brain(tmp_path, {"doctor": Result(0, doctor(222))})
    assert ctx.app.before_start(ctx, PENDING) == (
        "the brain schema is 222, the rehearsal expected 221", [])


def test_before_start_doctor_not_ok(tmp_path):
    ctx = brain(tmp_path, {"doctor": Result(0, "", "Error [pglite_busy]\n")})
    reason, details = ctx.app.before_start(ctx, PENDING)
    assert reason == "gbrain doctor is not ok: doctor printed no JSON"
    assert details == [("gbrain doctor (last lines)", "Error [pglite_busy]")]


# --- maintenance (dream) --------------------------------------------------------------

def test_maintenance_runs_dream_with_network_and_keys(tmp_path):
    ctx = brain(tmp_path)
    assert ctx.app.maintenance(ctx) is None
    ((image, args, argv),) = ctx.runs
    assert (image, args) == ("localhost/gbrain:current", ["dream"])
    assert "--network=none" not in argv and "--env-file" in argv
    assert f"{ctx.conf.data_dir}:/data:Z" in argv
    assert ctx.sh.timeouts[ctx.sh.calls.index(argv)] == 3600


def test_maintenance_failure_line_redacts_env_values(tmp_path):
    ctx = brain(tmp_path, {"dream": Result(1, "phase embed\n",
                                           "401 Unauthorized for key sk-secret123\n")})
    ctx.paths.app_env.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.app_env.write_text("OPENAI_API_KEY=sk-secret123\nFAKE=1\n")
    assert ctx.app.maintenance(ctx) == "401 Unauthorized for key ***"


def test_maintenance_failure_without_output(tmp_path):
    ctx = brain(tmp_path, {"dream": Result(3, "", "")})
    assert ctx.app.maintenance(ctx) == "exit 3"


# --- setup, end to end with fakes ------------------------------------------------------

def gsetup(tmp_path, monkeypatch):
    from tests.test_setup import args
    home = tmp_path / "home"
    p = Paths(home, "gbrain")
    p.conf_dir.mkdir(parents=True)
    p.conf_file.write_text(f"app = gbrain\ndashboard.public_url = {URL}\n")
    ctx = brain(tmp_path)
    ctx.sh.on("systemctl").on("systemctl", "--user", "is-active", out="inactive\n")
    ctx.sh.on("podman", "tag")
    monkeypatch.setattr(setup, "which", lambda t: None)
    monkeypatch.setattr(gb, "which", lambda t: None)
    monkeypatch.setattr(ctx.app, "releases", lambda c: {TAG: "c"})
    monkeypatch.setattr(ctx.app, "published", lambda c, tags: {TAG})
    monkeypatch.setattr(ctx.app, "fetch", lambda c, t, commit: {
        "tag": t, "id": "sha256:n", "ref": f"build:{t}", "digest": "d", "commit": commit})
    monkeypatch.setattr(setup.service, "post_start_check", lambda c: None)
    return ctx, home, args


def test_setup_fresh_install_initializes_the_brain(tmp_path, monkeypatch, capsys):
    ctx, home, args = gsetup(tmp_path, monkeypatch)
    rc = setup.service_phase(ctx, args(as_service=True))
    out = capsys.readouterr().out.replace(str(home), "~H")
    token = parse_kv(ctx.paths.app_env.read_text())["GBRAIN_ADMIN_BOOTSTRAP_TOKEN"]
    assert rc == 0 and token not in out
    assert out == (
        "OK: no existing gbrain found: fresh install\n"
        "OK: admin token generated in ~H/.config/talaria/gbrain.env (GBRAIN_ADMIN_BOOTSTRAP_TOKEN)\n"
        "NOTE: gbrain downloads are checked against the release's sha256 digest only (gh is "
        "not installed); install gh and run `gh auth login` as this account to also verify "
        "their build provenance\n"
        "OK: gbrain v0.60.116.0 pulled and verified\n"
        "OK: gbrain brain initialized in ~H/gbrain-data (PGLite, schema 221)\n"
        "OK: gbrain is running at http://127.0.0.1:3131 (admin UI /admin; its token is "
        "GBRAIN_ADMIN_BOOTSTRAP_TOKEN in ~H/.config/talaria/gbrain.env). MCP connectors use "
        f"{URL}/mcp once you publish it (README: gbrain)\n")
    assert ctx.paths.quadlet.exists()


def test_setup_stops_without_the_public_url(tmp_path, capsys):
    from tests.test_setup import args
    ctx = make_test_ctx(tmp_path, app="gbrain")
    rc = setup.service_phase(ctx, args(as_service=True))
    assert rc == 1
    assert capsys.readouterr().out == ("OK: no existing gbrain found: fresh install\n"
                                       f"STOP: {gb.NEEDS_URL}\n")
    assert ctx.paths.conf_file.read_text().startswith("# Talaria settings; see README.\napp = gbrain\n")
    assert ctx.sh.calls == []


def test_setup_plan_names_the_admin_token(tmp_path, capsys):
    from tests.test_setup import args
    ctx = make_test_ctx(tmp_path, app="gbrain")
    assert setup.service_phase(ctx, args(as_service=True, plan=True)) == 0
    assert capsys.readouterr().out == ("OK: no existing gbrain found: fresh install\n"
                                       "PLAN: admin token, install units, start gbrain, verify\n")

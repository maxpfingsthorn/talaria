import hashlib
import sqlite3
import subprocess

import pytest

from talaria import apps, rehearse
from talaria.conf import write_env_value
from talaria.images import RevisionMismatch
from tests.fakes import make_test_ctx

BIN = b"\x7fELF fake clawvisor"
SUM = hashlib.sha256(BIN).hexdigest()


def cctx(tmp_path, files):
    ctx = make_test_ctx(tmp_path, app="clawvisor")

    def download(url, dest, max_bytes):
        name = url.rsplit("/", 1)[1]
        if name not in files:
            return 404
        dest.write_bytes(files[name])
        return 200
    ctx.download = download
    ctx.sh.on("uname", "-m", out="x86_64\n")
    ctx.sh.on("podman", "build", out="sha256:built\n")
    ctx.sh.on("podman", "run", out="clawvisor-server 0.9.10\n")
    return ctx


def test_releases_are_plain_semver_tags(tmp_path, monkeypatch):
    from talaria.apps import clawvisor as cv
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    seen = {}

    def ls_remote(sh, repo):
        seen["sh"], seen["repo"] = sh, repo
        return {"v0.9.10": "a", "v0.9.11-rc1": "b", "latest": "c", "v0.9.9": "d"}
    monkeypatch.setattr(cv, "_ls_remote", ls_remote)
    assert ctx.app.releases(ctx) == {"v0.9.10": "a", "v0.9.9": "d"}
    assert seen == {"sh": ctx.sh, "repo": ctx.conf.repo}


def test_tag_key_rejects_a_non_release_tag_by_name(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    with pytest.raises(ValueError, match="not a release tag: 'latest'"):
        ctx.app.tag_key("latest")


def test_fetch_verifies_checksum_and_builds(tmp_path):
    ctx = cctx(tmp_path, {"checksums.txt": f"{SUM}  clawvisor-server-linux-amd64\n".encode(),
                          "clawvisor-server-linux-amd64": BIN})
    rec = ctx.app.fetch(ctx, "v0.9.10", "abc")
    assert rec == {"tag": "v0.9.10", "id": "sha256:built", "digest": f"sha256:{SUM}",
                   "ref": "build:v0.9.10", "commit": "abc"}
    build = ctx.sh.called("podman", "build")[0]
    assert "--label" in build and "org.opencontainers.image.revision=abc" in build
    assert "localhost/clawvisor:v0.9.10" in build


def test_fetch_removes_a_leftover_staging_dir_first(tmp_path):
    """A crashed previous fetch can leave build-<tag> behind; it must be cleared before
    work.mkdir(mode=0o700), which has no exist_ok and would otherwise raise."""
    ctx = cctx(tmp_path, {"checksums.txt": f"{SUM}  clawvisor-server-linux-amd64\n".encode(),
                          "clawvisor-server-linux-amd64": BIN})
    work = ctx.paths.staging / "build-v0.9.10"
    work.mkdir(parents=True)
    (work / "leftover.txt").write_text("stale")
    ctx.app.fetch(ctx, "v0.9.10", "abc")


def test_fetch_exact_build_and_version_check_mechanics(tmp_path):
    """Pins every literal in the build and version-check commands, the build context
    directory's mode, the Containerfile's exact name and templated content, and that the
    staging directory is gone afterwards -- the details a loose substring check lets
    drift."""
    from talaria.apps.clawvisor import BASE
    ctx = cctx(tmp_path, {"checksums.txt": f"{SUM}  clawvisor-server-linux-amd64\n".encode(),
                          "clawvisor-server-linux-amd64": BIN})
    work = ctx.paths.staging / "build-v0.9.10"
    seen = {}

    def build_fn(argv, input):
        from talaria.shell import Result
        seen["build_argv"] = argv
        seen["mode"] = work.stat().st_mode & 0o777
        seen["containerfile"] = (work / "Containerfile").read_text()
        return Result(0, "sha256:built\n", "")
    ctx.sh.on("podman", "build", fn=build_fn)
    rec = ctx.app.fetch(ctx, "v0.9.10", "abc")
    assert seen["build_argv"] == [
        "podman", "build", "-q", "--pull=missing", "--timestamp", "0",
        "--label", "org.opencontainers.image.revision=abc",
        "--label", "org.opencontainers.image.version=v0.9.10",
        "-t", "localhost/clawvisor:v0.9.10", str(work)]
    assert seen["mode"] == 0o700
    assert seen["containerfile"] == (
        f"FROM {BASE}\nCOPY --chmod=0755 clawvisor-server /clawvisor-server\n"
        "EXPOSE 25297\nUSER 65532:65532\nENTRYPOINT [\"/clawvisor-server\"]\nCMD [\"server\"]\n")
    assert not work.exists()   # the finally block's cleanup used the real path
    run = ctx.sh.called("podman", "run", "--rm")[0]
    assert run == ["podman", "run", "--rm", "--network=none", "sha256:built", "--version"]
    assert ctx.sh.timeouts[ctx.sh.calls.index(run)] == 120
    assert ctx.sh.timeouts[ctx.sh.calls.index(seen["build_argv"])] == 1800
    assert rec == {"tag": "v0.9.10", "id": "sha256:built", "digest": f"sha256:{SUM}",
                   "ref": "build:v0.9.10", "commit": "abc"}


def test_fetch_passes_the_right_byte_caps_to_each_download(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.sh.on("uname", "-m", out="x86_64\n")
    ctx.sh.on("podman", "build", out="sha256:built\n")
    ctx.sh.on("podman", "run", out="clawvisor-server 0.9.10\n")
    seen = {}

    def download(url, dest, max_bytes):
        name = url.rsplit("/", 1)[1]
        seen[name] = max_bytes
        dest.write_bytes(f"{SUM}  clawvisor-server-linux-amd64\n".encode()
                         if name == "checksums.txt" else BIN)
        return 200
    ctx.download = download
    ctx.app.fetch(ctx, "v0.9.10", "abc")
    from talaria.apps.clawvisor import MAX_BINARY
    assert seen == {"checksums.txt": 1 << 20, "clawvisor-server-linux-amd64": MAX_BINARY}


def test_oversized_binary_asset_message_names_the_right_asset_and_tag(tmp_path):
    """Guards the second _get() call's own tag/label: the checksums.txt download always
    succeeds here, so only the binary asset's cap is hit, and the message must name that
    asset and the real tag, not whatever the checksums call happened to use."""
    from talaria.ctx import TooLarge
    from talaria.apps.clawvisor import MAX_BINARY
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.sh.on("uname", "-m", out="x86_64\n")

    def download(url, dest, max_bytes):
        if url.endswith("checksums.txt"):
            dest.write_bytes(f"{SUM}  clawvisor-server-linux-amd64\n".encode())
            return 200
        raise TooLarge(f"{url} is larger than {max_bytes} bytes")
    ctx.download = download
    with pytest.raises(RevisionMismatch,
                       match=rf"v0\.9\.10: clawvisor-server-linux-amd64 exceeds the "
                             rf"{MAX_BINARY}-byte download limit"):
        ctx.app.fetch(ctx, "v0.9.10", "abc")


def test_binary_version_check_uses_the_last_token_not_a_fixed_index(tmp_path):
    """The version-check output can plausibly carry more than two tokens (e.g. build
    metadata before the version); only the very last token is the version."""
    ctx = cctx(tmp_path, {"checksums.txt": f"{SUM}  clawvisor-server-linux-amd64\n".encode(),
                          "clawvisor-server-linux-amd64": BIN})
    ctx.sh.on("podman", "run", out="clawvisor-server build 2026 0.9.10\n")
    rec = ctx.app.fetch(ctx, "v0.9.10", "abc")
    assert rec["tag"] == "v0.9.10"


def test_binary_version_mismatch_message_reports_the_last_token(tmp_path):
    ctx = cctx(tmp_path, {"checksums.txt": f"{SUM}  clawvisor-server-linux-amd64\n".encode(),
                          "clawvisor-server-linux-amd64": BIN})
    ctx.sh.on("podman", "run", out="clawvisor-server build 2026 0.9.9\n")
    with pytest.raises(RevisionMismatch, match=r"reports version 0\.9\.9$"):
        ctx.app.fetch(ctx, "v0.9.10", "abc")


def test_binary_version_check_with_empty_output_reports_a_question_mark(tmp_path):
    """An empty podman run --version output must report '?', not crash with an
    IndexError while formatting the message."""
    ctx = cctx(tmp_path, {"checksums.txt": f"{SUM}  clawvisor-server-linux-amd64\n".encode(),
                          "clawvisor-server-linux-amd64": BIN})
    ctx.sh.on("podman", "run", out="")
    with pytest.raises(RevisionMismatch, match=r"reports version \?$"):
        ctx.app.fetch(ctx, "v0.9.10", "abc")


def test_reacquire_rebuilds_the_same_tag_reproducibly(tmp_path):
    """Guards I1: reacquire must re-run fetch() for the record's own tag/commit, and the
    build must be reproducible (--timestamp 0) so the same verified binary and pinned
    base yield the same image ID as the original build -- otherwise images.ensure()'s
    post-reacquire existence check would always fail."""
    ctx = cctx(tmp_path, {"checksums.txt": f"{SUM}  clawvisor-server-linux-amd64\n".encode(),
                          "clawvisor-server-linux-amd64": BIN})
    ctx.app.reacquire(ctx, {"tag": "v0.9.10", "commit": "abc", "id": "sha256:gone"})
    build = ctx.sh.called("podman", "build")[0]
    assert "org.opencontainers.image.revision=abc" in build
    assert "org.opencontainers.image.version=v0.9.10" in build
    assert "localhost/clawvisor:v0.9.10" in build
    assert "--timestamp" in build and build[build.index("--timestamp") + 1] == "0"


def test_unsupported_architecture_is_permanent_not_a_keyerror(tmp_path):
    """Guards M4: an unrecognized `uname -m` (e.g. armv7l) must not escape as a raw
    KeyError -- that surfaces to Telegram as 'failed unexpectedly' every day."""
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.sh.on("uname", "-m", out="armv7l\n")
    with pytest.raises(RevisionMismatch, match="unsupported architecture: armv7l"):
        ctx.app.fetch(ctx, "v0.9.10", "abc")
    assert ctx.sh.called("podman", "build") == []


def test_checksum_mismatch_is_permanent(tmp_path):
    ctx = cctx(tmp_path, {"checksums.txt": f"{'0' * 64}  clawvisor-server-linux-amd64\n".encode(),
                          "clawvisor-server-linux-amd64": BIN})
    with pytest.raises(RevisionMismatch, match="checksum"):
        ctx.app.fetch(ctx, "v0.9.10", "abc")
    assert ctx.sh.called("podman", "build") == []


def test_missing_assets_are_transient(tmp_path):
    ctx = cctx(tmp_path, {})
    with pytest.raises(rehearse.Transient, match="release assets of v0.9.10 are not published yet"):
        ctx.app.fetch(ctx, "v0.9.10", "abc")


def test_binary_version_must_match_the_tag(tmp_path):
    ctx = cctx(tmp_path, {"checksums.txt": f"{SUM}  clawvisor-server-linux-amd64\n".encode(),
                          "clawvisor-server-linux-amd64": BIN})
    ctx.sh.on("podman", "run", out="clawvisor-server 0.9.9\n")
    with pytest.raises(RevisionMismatch, match="reports version 0.9.9"):
        ctx.app.fetch(ctx, "v0.9.10", "abc")


def test_oversized_asset_is_permanent_not_a_traceback(tmp_path):
    from talaria.ctx import TooLarge
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.sh.on("uname", "-m", out="x86_64\n")

    def download(url, dest, max_bytes):
        raise TooLarge(f"{url} is larger than {max_bytes} bytes")
    ctx.download = download
    with pytest.raises(RevisionMismatch, match="checksums.txt exceeds the 1048576-byte"
                                                " download limit"):
        ctx.app.fetch(ctx, "v0.9.10", "abc")
    assert ctx.sh.called("podman", "build") == []


def test_get_only_treats_toolarge_as_permanent(tmp_path):
    """_get must narrow on the dedicated TooLarge, not any OSError: a mid-stream reset,
    disk-full or TLS failure from ctx.download must not be mistaken for the
    oversized-asset case and turned into a misleading Permanent/"exceeds the limit"."""
    from talaria.apps import clawvisor as cv
    from talaria.ctx import TooLarge
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    dest = tmp_path / "f"

    def too_large(url, dest, max_bytes):
        raise TooLarge(f"{url} is larger than {max_bytes} bytes")
    ctx.download = too_large
    with pytest.raises(RevisionMismatch, match="exceeds the 10-byte download limit"):
        cv._get(ctx, "http://x/y", dest, 10, "v0.9.10", "asset")

    def reset(url, dest, max_bytes):
        raise ConnectionResetError("connection reset by peer")
    ctx.download = reset
    with pytest.raises(ConnectionResetError):
        cv._get(ctx, "http://x/y", dest, 10, "v0.9.10", "asset")


def test_network_error_during_download_is_still_transient(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.sh.on("uname", "-m", out="x86_64\n")
    ctx.download = lambda url, dest, max_bytes: 0   # e.g. a DNS failure or connection refused
    with pytest.raises(rehearse.Transient, match="release assets of v0.9.10 are not published yet"):
        ctx.app.fetch(ctx, "v0.9.10", "abc")


def make_db(path, names):
    c = sqlite3.connect(path)
    c.execute("create table schema_migrations (name text primary key, applied_at text)")
    c.executemany("insert into schema_migrations values (?, 'x')", [(n,) for n in names])
    c.commit()
    c.close()


def test_rehearse_reports_new_migrations(tmp_path):
    from talaria.apps import clawvisor as cv
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    copy, stage = tmp_path / "copy", tmp_path / "stage"
    copy.mkdir(); stage.mkdir()
    make_db(copy / "clawvisor.db", ["001_init.sql", "054_x.sql"])

    def run_new(argv, input):
        make_db_add = sqlite3.connect(copy / "clawvisor.db")
        make_db_add.execute("insert into schema_migrations values ('055_y.sql', 'x')")
        make_db_add.commit(); make_db_add.close()
        from talaria.shell import Result
        return Result(0, "cid\n", "")
    ctx.sh.on("podman", "rm").on("podman", "run", fn=run_new)
    ctx.sh.on("podman", "exec").on("podman", "stop").on("podman", "logs", out="")
    rep = ctx.app.rehearse(ctx, {}, {"tag": "v0.9.10", "id": "sha256:i", "digest": "d"},
                           copy, stage)
    assert (rep["before"], rep["after"], rep["new"]) == (2, 3, ["055_y.sql"])
    assert (rep["tag"], rep["digest"], rep["latest"]) == ("v0.9.10", "d", "055_y.sql")
    # every argv and timeout, in order: leftover cleanup, start, one healthcheck poll,
    # then best-effort stop + rm in the finally block
    envs = [a for e in cv.ENV for a in ("-e", e)]
    run_argv = ["podman", "run", "-d", "--name", cv.NAME, "--network=none",
               "--userns=keep-id:uid=65532,gid=65532", "-v", f"{copy}:/data:Z",
               "--env-file", str(ctx.paths.app_env), *envs, "sha256:i"]
    assert ctx.sh.calls == [
        ["podman", "rm", "-f", cv.NAME],
        run_argv,
        ["podman", "exec", cv.NAME, "/clawvisor-server", "healthcheck"],
        ["podman", "stop", "-t", "30", cv.NAME],
        ["podman", "rm", "-f", cv.NAME],
    ]
    assert ctx.sh.timeouts == [120, 300, 30, 120, 120]
    lines, blocks = ctx.app.report_lines(ctx, rep)
    assert "Database migrations: 2 → 3" in lines
    assert blocks == [("Migrations that will run (1)", "055_y.sql")]


def test_rehearse_cleanup_tolerates_failed_stop_and_rm(tmp_path):
    """The leading leftover-cleanup and the finally block's stop/rm are all best-effort
    (check=False): a rehearsal that otherwise succeeds must not be derailed by podman
    reporting a nonzero exit from any of them (e.g. 'no such container')."""
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    copy, stage = tmp_path / "copy", tmp_path / "stage"
    copy.mkdir(); stage.mkdir()
    make_db(copy / "clawvisor.db", ["001_init.sql"])
    ctx.sh.on("podman", "rm", rc=1).on("podman", "run", out="cid\n")
    ctx.sh.on("podman", "exec").on("podman", "stop", rc=1)
    rep = ctx.app.rehearse(ctx, {}, {"tag": "v0.9.10", "id": "sha256:i"}, copy, stage)
    assert rep["tag"] == "v0.9.10"


def test_rehearse_waits_at_least_120s_even_with_a_low_settle_seconds(tmp_path):
    """Guards M3: Clawvisor runs migrations at startup and its log tables are never
    pruned, so a slow migration can exceed the usual settle window. The rehearsal must
    wait at least 120s for readiness regardless of a lower settle_seconds."""
    from talaria.shell import Result
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    copy, stage = tmp_path / "copy", tmp_path / "stage"
    copy.mkdir(); stage.mkdir()
    make_db(copy / "clawvisor.db", ["001_init.sql"])
    ctx.conf.settle_seconds = 10   # lower than the 120s floor
    calls = []

    def exec_fn(argv, input):
        calls.append(argv)
        return Result(0 if len(calls) >= 55 else 1, "", "")   # ready only after ~110s
    ctx.sh.on("podman", "rm").on("podman", "run", out="cid\n").on("podman", "stop")
    ctx.sh.on("podman", "exec", fn=exec_fn)
    rep = ctx.app.rehearse(ctx, {}, {"tag": "v0.9.10", "id": "sha256:i"}, copy, stage)
    assert rep["tag"] == "v0.9.10"
    assert len(calls) == 55


def test_rehearse_not_ready_is_permanent_with_log_tail(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    copy, stage = tmp_path / "copy", tmp_path / "stage"
    copy.mkdir(); stage.mkdir()
    make_db(copy / "clawvisor.db", ["001_init.sql"])
    ctx.sh.on("podman", "rm").on("podman", "run", out="cid\n").on("podman", "stop")
    ctx.sh.on("podman", "exec", rc=1).on("podman", "logs", out="boom: migration 055 failed\n")
    ctx.conf.settle_seconds = 10
    with pytest.raises(rehearse.Permanent, match="did not become ready") as e:
        ctx.app.rehearse(ctx, {}, {"tag": "v0.9.10", "id": "sha256:i"}, copy, stage)
    assert e.value.details == [("Log (last lines)", "boom: migration 055 failed")]
    # the 120s floor applies even though settle_seconds = 10: exactly 60 polls, 2s apart
    assert len(ctx.sh.called("podman", "exec")) == 60
    assert ctx.clock.slept == 120
    logs = ctx.sh.called("podman", "logs")[0]
    assert logs == ["podman", "logs", "--tail", "20", "talaria-rehearse"]
    assert ctx.sh.timeouts[ctx.sh.calls.index(logs)] == 60


def test_rehearse_tolerates_a_failed_log_tail(tmp_path):
    """The log-tail fetch after a failed readiness poll is check=False too: podman
    itself refusing to show logs must not crash out with CommandError instead of the
    expected Permanent."""
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    copy, stage = tmp_path / "copy", tmp_path / "stage"
    copy.mkdir(); stage.mkdir()
    make_db(copy / "clawvisor.db", ["001_init.sql"])
    ctx.sh.on("podman", "rm").on("podman", "run", out="cid\n").on("podman", "stop")
    ctx.sh.on("podman", "exec", rc=1).on("podman", "logs", rc=1, err="no such container\n")
    ctx.conf.settle_seconds = 10
    with pytest.raises(rehearse.Permanent, match="did not become ready"):
        ctx.app.rehearse(ctx, {}, {"tag": "v0.9.10", "id": "sha256:i"}, copy, stage)


def test_rehearse_with_no_migrations_reports_latest_as_none(tmp_path):
    """An empty schema_migrations (nothing applied yet) must report latest=None, not
    crash trying to index the last element of an empty list."""
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    copy, stage = tmp_path / "copy", tmp_path / "stage"
    copy.mkdir(); stage.mkdir()
    make_db(copy / "clawvisor.db", [])
    ctx.sh.on("podman", "rm").on("podman", "run", out="cid\n").on("podman", "exec")
    ctx.sh.on("podman", "stop")
    rep = ctx.app.rehearse(ctx, {}, {"tag": "v0.9.10", "id": "sha256:i"}, copy, stage)
    assert (rep["before"], rep["after"], rep["latest"]) == (0, 0, None)


def test_rehearse_run_failure_is_transient_and_cleans_up(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    copy, stage = tmp_path / "copy", tmp_path / "stage"
    copy.mkdir(); stage.mkdir()
    make_db(copy / "clawvisor.db", ["001_init.sql"])
    ctx.sh.on("podman", "rm").on("podman", "run", rc=1, err="no space left on device\n")
    ctx.sh.on("podman", "stop")
    with pytest.raises(rehearse.Transient, match="could not start"):
        ctx.app.rehearse(ctx, {}, {"tag": "v0.9.10", "id": "sha256:i"}, copy, stage)
    assert ctx.sh.called("podman", "stop", "-t", "30", "talaria-rehearse")
    # leading cleanup of a leftover container, plus the cleanup after the failed start
    assert len(ctx.sh.called("podman", "rm", "-f", "talaria-rehearse")) == 2
    assert ctx.sh.called("podman", "exec") == []   # never reached the readiness poll


def test_rehearse_run_failure_message_is_redacted(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    copy, stage = tmp_path / "copy", tmp_path / "stage"
    copy.mkdir(); stage.mkdir()
    make_db(copy / "clawvisor.db", ["001_init.sql"])
    write_env_value(ctx.paths.app_env, "JWT_SECRET", "sekrit-token-value")
    ctx.sh.on("podman", "rm").on("podman", "run", rc=1,
                                  err="boom: sekrit-token-value leaked\n")
    ctx.sh.on("podman", "stop")
    with pytest.raises(rehearse.Transient) as e:
        ctx.app.rehearse(ctx, {}, {"tag": "v0.9.10", "id": "sha256:i"}, copy, stage)
    assert "sekrit-token-value" not in str(e.value)
    assert "***" in str(e.value)


def test_rehearse_run_timeout_is_transient_and_cleans_up(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    copy, stage = tmp_path / "copy", tmp_path / "stage"
    copy.mkdir(); stage.mkdir()
    make_db(copy / "clawvisor.db", ["001_init.sql"])

    def timeout_run(argv, input):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=300)
    ctx.sh.on("podman", "rm").on("podman", "run", fn=timeout_run)
    ctx.sh.on("podman", "stop")
    with pytest.raises(rehearse.Transient, match="could not start"):
        ctx.app.rehearse(ctx, {}, {"tag": "v0.9.10", "id": "sha256:i"}, copy, stage)
    assert ctx.sh.called("podman", "stop", "-t", "30", "talaria-rehearse")
    assert len(ctx.sh.called("podman", "rm", "-f", "talaria-rehearse")) == 2


def test_log_tail_redacts_env_secrets_and_vault_key(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    copy, stage = tmp_path / "copy", tmp_path / "stage"
    copy.mkdir(); stage.mkdir()
    make_db(copy / "clawvisor.db", ["001_init.sql"])
    write_env_value(ctx.paths.app_env, "JWT_SECRET", "sekrit-token-value")
    (copy / "vault.key").write_text("vault-key-bytes-xyz\n")
    ctx.sh.on("podman", "rm").on("podman", "run", out="cid\n").on("podman", "stop")
    ctx.sh.on("podman", "exec", rc=1)
    ctx.sh.on("podman", "logs", out="boot failed, env dump: JWT_SECRET=sekrit-token-value "
                                     "vault=vault-key-bytes-xyz\n")
    ctx.conf.settle_seconds = 10
    with pytest.raises(rehearse.Permanent) as e:
        ctx.app.rehearse(ctx, {}, {"tag": "v0.9.10", "id": "sha256:i"}, copy, stage)
    tail = e.value.details[0][1]
    assert "sekrit-token-value" not in tail
    assert "vault-key-bytes-xyz" not in tail
    assert "***" in tail


def test_redact_survives_undecodable_app_env(tmp_path):
    """A corrupt/binary app_env must not crash redaction: redact what it can (the vault
    key) and leave the rest of the text alone rather than raising UnicodeDecodeError."""
    from talaria.apps import clawvisor as cv
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.paths.app_env.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.app_env.write_bytes(b"\xff\xfe not valid utf-8\n")
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "vault.key").write_text("vault-key-bytes-xyz\n")
    text = cv._redact(ctx, copy, "boot failed, dump: vault=vault-key-bytes-xyz")
    assert "vault-key-bytes-xyz" not in text
    assert "***" in text


def test_redact_skips_short_values_but_keeps_jwt_secret_and_vault_key(tmp_path):
    """Values under 4 chars would shred ordinary log text if redacted, so they're left
    alone -- except JWT_SECRET and the vault key, which are always redacted even when
    short, since those two are secrets by construction regardless of length."""
    from talaria.apps import clawvisor as cv
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    write_env_value(ctx.paths.app_env, "JWT_SECRET", "abc")
    write_env_value(ctx.paths.app_env, "TINY", "xyz")
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "vault.key").write_text("qrs\n")
    text = cv._redact(ctx, copy, "dump: TINY=xyz JWT_SECRET=abc vault=qrs")
    assert "abc" not in text   # JWT_SECRET: always redacted, even though short
    assert "qrs" not in text   # vault key: always redacted, even though short
    assert "xyz" in text       # an ordinary value under 4 chars is left alone
    assert "***" in text


def test_redact_keeps_the_four_char_boundary_value(tmp_path):
    """len(v) >= 4 means exactly 4 chars must still be redacted, not just 5+."""
    from talaria.apps import clawvisor as cv
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    write_env_value(ctx.paths.app_env, "TOKEN", "abcd")
    text = cv._redact(ctx, tmp_path, "dump: abcd")
    assert text == "dump: ***"


def test_redact_replaces_longest_value_first_to_avoid_partial_leaks(tmp_path):
    """If one secret's value is a substring of another's (here, the vault key's bytes
    happen to contain the JWT secret), the longer one must be masked whole -- masking
    the shorter one first would leave the rest of the longer secret exposed in the
    clear. This also pins the exact '***' replacement text."""
    from talaria.apps import clawvisor as cv
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    write_env_value(ctx.paths.app_env, "JWT_SECRET", "bbbb")
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "vault.key").write_text("aaaabbbbaaaa\n")
    text = cv._redact(ctx, copy, "start aaaabbbbaaaa end bbbb tail")
    assert text == "start *** end *** tail"


def test_redact_refuses_a_symlinked_vault_key(tmp_path):
    """Talaria never follows symlinks in the data dir (see README Security): a vault.key
    that is a symlink must not be read, even if it resolves to a real file."""
    from talaria.apps import clawvisor as cv
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    copy = tmp_path / "copy"
    copy.mkdir()
    target = tmp_path / "outside-secret.txt"
    target.write_text("totally-secret-value\n")
    (copy / "vault.key").symlink_to(target)
    text = cv._redact(ctx, copy, "dump: totally-secret-value")
    assert "totally-secret-value" in text   # never followed, so never added to the mask list


def test_redact_vault_key_read_failure_adds_no_spurious_value(tmp_path):
    """A vault.key that can't be decoded must fail safe to '' (skipped, since empty
    values are never added) rather than to some non-empty placeholder that would then
    get redacted out of unrelated log text."""
    from talaria.apps import clawvisor as cv
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "vault.key").write_bytes(b"\xff\xfe not valid utf-8")
    text = cv._redact(ctx, copy, "dump: XXXX should not be touched")
    assert text == "dump: XXXX should not be touched"


def test_migrations_missing_file_and_missing_table(tmp_path):
    from talaria.apps.clawvisor import migrations
    assert migrations(tmp_path / "nope.db") == []
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    assert migrations(db) == []


def test_before_start_and_quadlet_vars_are_the_base_no_ops(tmp_path):
    """Clawvisor has nothing to run before the container starts and no extra quadlet
    variables, so it relies on App's defaults rather than redefining them."""
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    assert ctx.app.before_start(ctx, {}) == (None, [])
    assert ctx.app.quadlet_vars(ctx) == {}


def test_report_lines_with_no_new_migrations(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    lines, blocks = ctx.app.report_lines(ctx, {"before": 3, "after": 3, "new": []})
    assert lines == ["Database migrations: 3 → 3"]
    assert blocks == []


def test_report_lines_joins_multiple_new_migrations_with_newlines(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    lines, blocks = ctx.app.report_lines(
        ctx, {"before": 3, "after": 5, "new": ["055_a.sql", "056_b.sql"]})
    assert blocks == [("Migrations that will run (2)", "055_a.sql\n056_b.sql")]


def test_prepare_generates_secrets_once(tmp_path):
    import base64
    from talaria.conf import parse_kv
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    out = ctx.app.prepare(ctx)
    env = parse_kv(ctx.paths.app_env.read_text())
    key = (ctx.conf.data_dir / "vault.key").read_text()
    assert len(env["JWT_SECRET"]) == 64 and len(key.strip()) == 44
    assert len(base64.b64decode(key.strip())) == 32   # the real byte count, not just the
                                                       # base64 text length (32 and 33
                                                       # bytes both encode to 44 chars)
    assert oct(ctx.paths.app_env.stat().st_mode & 0o777) == "0o600"
    assert oct((ctx.conf.data_dir / "vault.key").stat().st_mode & 0o777) == "0o600"
    assert oct(ctx.conf.data_dir.stat().st_mode & 0o777) == "0o700"
    assert out and all("secret" not in l.lower() or "generated" in l for l in out)
    assert ctx.app.prepare(ctx) == []                       # second run: nothing new
    assert parse_kv(ctx.paths.app_env.read_text())["JWT_SECRET"] == env["JWT_SECRET"]
    assert (ctx.conf.data_dir / "vault.key").read_text() == key


def test_prepare_refuses_existing_db_without_vault_key(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    make_db(ctx.conf.data_dir / "clawvisor.db", ["001_init.sql"])
    with pytest.raises(ValueError, match="vault.key is missing"):
        ctx.app.prepare(ctx)


def test_health_and_after_start(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.conf.dashboard_bind, ctx.conf.tailscale_ip = "tailscale", "100.64.0.1"
    ctx.http_get = lambda url, t: (200, b'{"db":"ok","status":"ok","vault":"ok"}') \
        if url == "http://100.64.0.1:25297/ready" else (0, b"")
    assert ctx.app.health(ctx) is None
    ctx.http_get = lambda url, t: (200, b'{"db":"ok","status":"ok","vault":"locked"}')
    assert ctx.app.health(ctx) == "/ready reports vault locked"
    make_db(ctx.conf.data_dir / "clawvisor.db", ["001_init.sql", "002_x.sql"])
    assert ctx.app.after_start(ctx, {"migrations_after": 2}) is None
    assert ctx.app.after_start(ctx, {"migrations_after": 3}) == (
        "the database has 2 migrations, the rehearsal expected 3")


def test_quadlet(tmp_path):
    from talaria import units
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.conf.dashboard_bind, ctx.conf.tailscale_ip = "tailscale", "100.64.0.1"
    q = units.render_quadlet(ctx)
    for line in ("Image=localhost/clawvisor:current", "ReadOnly=true",
                 "UserNS=keep-id:uid=65532,gid=65532", f"Volume={ctx.conf.data_dir}:/data:Z",
                 "PublishPort=100.64.0.1:25297:25297", "Environment=CLAWVISOR_AUTO_UPDATE_ENABLED=false",
                 f"EnvironmentFile={ctx.paths.app_env}"):
        assert f"\n{line}\n" in q


def test_quadlet_environment_keys_match_the_rehearsal_env(tmp_path):
    """The template's Environment= lines and the rehearsal's ENV list must name the same
    variables, so a rehearsal keeps testing what production actually runs with."""
    from talaria.apps.clawvisor import ENV
    from talaria.ctx import Paths
    text = (Paths.templates_dir / "clawvisor.container").read_text()
    template_keys = {
        pair.split("=", 1)[0]
        for line in text.splitlines() if line.startswith("Environment=")
        for pair in line[len("Environment="):].split()
    }
    env_keys = {e.split("=", 1)[0] for e in ENV}
    assert template_keys == env_keys


def test_health_rejects_non_object_json(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    for body in (b"[]", b"null", b'"ok"', b"42"):
        ctx.http_get = lambda url, t, body=body: (200, body)
        assert ctx.app.health(ctx) == "/ready did not answer a JSON object"


def test_health_calls_http_get_with_the_exact_url_and_a_5s_timeout(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    seen = {}

    def http_get(url, timeout):
        seen["url"], seen["timeout"] = url, timeout
        return 200, b'{"db":"ok","status":"ok","vault":"ok"}'
    ctx.http_get = http_get
    assert ctx.app.health(ctx) is None
    assert seen == {"url": "http://127.0.0.1:25297/ready", "timeout": 5.0}


def test_health_bad_status_code_reports_exact_fallback_text(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.http_get = lambda url, t: (500, b"")
    assert ctx.app.health(ctx) == "/ready answered 500"
    ctx.http_get = lambda url, t: (0, b"")   # unreachable: no status code at all
    assert ctx.app.health(ctx) == "/ready answered nothing"


def test_health_invalid_json_reports_exact_text(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.http_get = lambda url, t: (200, b"not json")
    assert ctx.app.health(ctx) == "/ready did not answer JSON"


def test_health_joins_multiple_bad_fields_with_comma_space(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    ctx.http_get = lambda url, t: (200, b'{"db":"down","status":"ok","vault":"locked"}')
    assert ctx.app.health(ctx) == "/ready reports db down, vault locked"


def test_after_start_distinguishes_unreadable_db_from_empty(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    (ctx.conf.data_dir / "clawvisor.db").write_bytes(b"not a sqlite database at all")
    reason = ctx.app.after_start(ctx, {"migrations_after": 2})
    assert reason is not None
    assert reason.startswith("could not read migrations: ")
    assert "0 migrations" not in reason


def test_after_start_missing_db_is_not_an_error(tmp_path):
    """No clawvisor.db yet (e.g. a rehearsal that ran before the first real start) is a
    normal empty state, not a read error."""
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    assert ctx.app.after_start(ctx, {"migrations_after": 0}) is None
    assert ctx.app.after_start(ctx, {"migrations_after": 1}) == (
        "the database has 0 migrations, the rehearsal expected 1")


def test_vault_key_write_failure_leaves_no_empty_file(tmp_path, monkeypatch):
    """base64.b64encode is specific to the vault-key write (the JWT secret uses
    secrets.token_hex instead), so patching it only breaks that one step."""
    from talaria.apps import clawvisor as cv
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    monkeypatch.setattr(cv.base64, "b64encode",
                        lambda b: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        ctx.app.prepare(ctx)
    assert not (ctx.conf.data_dir / "vault.key").exists()
    monkeypatch.undo()
    # a later prepare(), once the disk has room again, can still create a real key
    ctx.app.prepare(ctx)
    assert len((ctx.conf.data_dir / "vault.key").read_text().strip()) == 44


def test_prepare_refuses_an_existing_empty_vault_key(tmp_path):
    ctx = make_test_ctx(tmp_path, app="clawvisor")
    (ctx.conf.data_dir / "vault.key").touch()
    with pytest.raises(ValueError, match="exists but is empty"):
        ctx.app.prepare(ctx)

"""Mutation survivors of the gbrain adapter and the maintenance window (v0.6.0): exact
timeouts, check flags, messages and edge inputs."""
import json
import os
import stat
from datetime import datetime, timezone

import pytest

from talaria import apps, maintain, state
from talaria.apps import gbrain as gb
from talaria.images import RevisionMismatch
from talaria.rehearse import Transient
from talaria.shell import Result
from tests.fakes import make_test_ctx
from tests.hubfakes import ex, make_hub, message
from tests.test_gbrain import BIN, GH, SUM, TAG, brain, doctor, gctx, release_json
from tests.test_maintain import mctx, st0, utc  # noqa: F401  (fixtures)


def record_checks(ctx):
    """Wrap ctx.sh.run so a test sees each call's check flag and timeout."""
    seen, real = [], ctx.sh.run

    def run(argv, *, input=None, check=True, timeout=None):
        seen.append((list(argv), check, timeout))
        return real(argv, input=input, check=check, timeout=timeout)
    ctx.sh.run = run
    return seen


# --- attestation_unavailable, oneoff, tail, last_line --------------------------------

def test_attestation_asks_for_gh_and_a_login_with_a_minute(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, gh="/usr/bin/gh")
    asked = []
    monkeypatch.setattr(gb, "which", lambda t: asked.append(t) or "/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status")
    seen = record_checks(ctx)
    assert gb.attestation_unavailable(ctx) is None
    assert asked == ["gh"]
    assert seen == [(["gh", "auth", "status"], False, 60)]


def test_oneoff_never_raises_on_a_failed_cleanup_or_run(tmp_path):
    ctx = brain(tmp_path)
    seen = record_checks(ctx)
    gb.oneoff(ctx, "img", tmp_path, ["doctor"])
    assert [(a[:2], c) for a, c, _ in seen] == [(["podman", "rm"], False),
                                                (["podman", "run"], False),
                                                (["podman", "rm"], False)]


def test_tail_keeps_the_last_lines_and_the_last_2000_chars():
    r = Result(0, "\n".join(f"line{i}" for i in range(30)) + "\n", "err\n")
    assert gb.tail(r, 3) == "line28\nline29\nerr"
    big = Result(0, "x" * 5000, "")
    assert gb.tail(big) == "x" * 2000
    assert gb.tail(Result(0, "".join(f"l{i}\n" for i in range(30)), "")).splitlines()[0] == "l10"


def test_last_line_prefers_stderr_then_stdout_and_cuts_at_300():
    assert gb.last_line(Result(1, "out\n", "")) == "out"
    assert gb.last_line(Result(1, "out\n", "e1\n\ne2\n")) == "e2"
    assert gb.last_line(Result(1, "", "y" * 400)) == "y" * 300
    assert gb.last_line(Result(7, "", "")) == "exit 7"


# --- parse_schema ---------------------------------------------------------------------

def test_parse_schema_finds_json_directly_behind_a_brace():
    out = '{{"checks":[{"name":"schema_version","status":"ok","message":"Version 3"}]}'
    assert gb.parse_schema(out) == 3


@pytest.mark.parametrize("out,msg", [
    ("{not json", "doctor printed no valid JSON"),
    ('{"checks": null}', "doctor reports no schema_version check"),
    ('{"checks": "x"}', "doctor reports no schema_version check"),
    ('{"checks":[{"name":"schema_version","status":"ok","message":"fine"}]}',
     "schema_version names no version: fine"),
    ('{"checks":[{"name":"schema_version","status":"ok"}]}',
     "schema_version names no version: None"),
])
def test_parse_schema_exact_reasons(out, msg):
    with pytest.raises(ValueError) as e:
        gb.parse_schema(out)
    assert str(e.value) == msg


# --- _redact ----------------------------------------------------------------------------

def test_redact_longest_first_and_only_values_of_four_or_more(tmp_path):
    ctx = brain(tmp_path)
    ctx.paths.app_env.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.app_env.write_text("A=abcd\nB=abcdef\nC=xyz\n")
    assert gb._redact(ctx, "abcdef abcd xyz") == "*** *** xyz"
    ctx.paths.app_env.write_text("A=bbbb\nB=abbbbc\n")      # longest first, not alphabetical
    assert gb._redact(ctx, "abbbbc") == "***"


# --- release digest and fetch -------------------------------------------------------------

def test_a_release_json_that_is_not_an_object_means_unpublished(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, api=(200, b"[]"))
    with pytest.raises(Transient, match="release assets of v0.60.116.0 are not published yet"):
        ctx.app.fetch(ctx, TAG, "abc")


def test_fetch_clears_a_stale_build_dir_and_builds_in_a_private_one(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    work = ctx.paths.staging / f"build-{TAG}"
    work.mkdir(parents=True)
    (work / "stale").write_text("old")
    modes = []
    orig = ctx.sh.rules[1][1]            # the podman build handler of gctx

    def build(argv, input):
        modes.append((stat.S_IMODE(os.stat(work).st_mode), sorted(os.listdir(work))))
        return orig(argv, input)
    ctx.sh.on("podman", "build", fn=build)
    ctx.app.fetch(ctx, TAG, "abc")
    assert modes == [(0o700, ["Containerfile", "gbrain"])]


def test_fetch_cleanup_tolerates_a_vanished_build_dir(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    import shutil

    def build(argv, input):
        from pathlib import Path
        shutil.rmtree(Path(argv[-1]))
        return Result(0, "sha256:built\n")
    ctx.sh.on("podman", "build", fn=build)
    assert ctx.app.fetch(ctx, TAG, "abc")["id"] == "sha256:built"


def test_digest_mismatch_message_names_twelve_characters(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, files={"gbrain-linux-x64": b"other"})
    import hashlib
    got = hashlib.sha256(b"other").hexdigest()
    with pytest.raises(RevisionMismatch) as e:
        ctx.app.fetch(ctx, TAG, "abc")
    assert str(e.value) == (f"{TAG}: gbrain-linux-x64 failed verification (sha256 {got[:12]}… "
                            f"is not the published {SUM[:12]}…)")


def test_attestation_failure_names_the_last_line_or_the_exit_code(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch, gh="/usr/bin/gh")
    ctx.sh.on("gh", "auth", "status")
    ctx.sh.on("gh", "attestation", rc=1, err="first\nsecond\nthird\n")
    with pytest.raises(RevisionMismatch) as e:
        ctx.app.fetch(ctx, TAG, "abc")
    assert str(e.value).endswith("(attestation: third)")
    ctx.sh.on("gh", "attestation", rc=4)
    with pytest.raises(RevisionMismatch) as e:
        ctx.app.fetch(ctx, TAG, "abc")
    assert str(e.value).endswith("(attestation: exit 4)")


def test_fetch_timeouts_and_the_version_message(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    ctx.app.fetch(ctx, TAG, "abc")
    t = {tuple(c[:2]): ctx.sh.timeouts[i] for i, c in enumerate(ctx.sh.calls)}
    assert t[("podman", "build")] == 1800 and t[("podman", "run")] == 120
    ctx.sh.on("podman", "run", out="gbrain 0.1.2.3\n")
    with pytest.raises(RevisionMismatch) as e:
        ctx.app.fetch(ctx, TAG, "abc")
    assert str(e.value) == f"{TAG}: the binary reports version 0.1.2.3"
    ctx.sh.on("podman", "run", out="what?\n")
    with pytest.raises(RevisionMismatch) as e:
        ctx.app.fetch(ctx, TAG, "abc")
    assert str(e.value) == f"{TAG}: the binary reports version ?"


def test_reacquire_passes_the_recorded_commit_or_an_empty_one(tmp_path, monkeypatch):
    ctx = gctx(tmp_path, monkeypatch)
    ctx.app.reacquire(ctx, {"tag": TAG, "commit": "abc"})
    ctx.app.reacquire(ctx, {"tag": TAG})
    labels = [c[c.index("--label") + 1] for c in ctx.sh.called("podman", "build")]
    assert labels == ["org.opencontainers.image.revision=abc",
                      "org.opencontainers.image.revision="]


def test_health_asks_with_five_seconds(tmp_path):
    ctx = make_test_ctx(tmp_path, app="gbrain")
    seen = []
    ctx.http_get = lambda url, timeout=99.0: (seen.append(timeout), (200, b'{"status":"ok"}'))[1]
    assert ctx.app.health(ctx) is None and seen == [5.0]


# --- prepare, initialize ---------------------------------------------------------------------

def test_prepare_creates_nested_private_data_and_a_32_byte_token(tmp_path):
    ctx = brain(tmp_path)
    ctx.conf.data_dir = tmp_path / "a" / "b" / "data"
    ctx.app.prepare(ctx)
    assert stat.S_IMODE(os.stat(ctx.conf.data_dir).st_mode) == 0o700
    token = gb.parse_kv(ctx.paths.app_env.read_text())[gb.TOKEN_KEY]
    assert len(token) == 43               # token_urlsafe(32)


def test_initialize_runs_on_the_data_dir_and_writes_the_init_marker(tmp_path):
    ctx = brain(tmp_path)
    ctx.app.initialize(ctx)
    mounts = {argv[argv.index("-v") + 1] for _, _, argv in ctx.runs}
    assert mounts == {f"{ctx.conf.data_dir}:/data:Z"}
    assert (ctx.conf.data_dir / ".talaria-init").read_text() == "1\n"


# --- maintenance window ----------------------------------------------------------------------

def test_window_start_drops_seconds_and_microseconds(mctx):
    mctx.clock.t = datetime(2026, 10, 8, 2, 15, 37, 123456, tzinfo=timezone.utc)
    assert maintain.window_start(mctx) == datetime(2026, 10, 8, 1, 30, tzinfo=timezone.utc)


def test_the_post_start_check_gets_the_context(mctx, monkeypatch):
    seen = []
    monkeypatch.setattr(maintain.service, "post_start_check",
                        lambda c: seen.append(c) or None)
    maintain.run(mctx, st0(), timer=True)
    assert seen == [mctx]


def test_the_hub_relays_the_app_name_into_commands_and_buttons(tmp_path, monkeypatch):
    h = make_hub(tmp_path, ("hermes", "clawvisor"), log=[])
    monkeypatch.setattr(apps.get("clawvisor"), "has_maintenance", True)
    ex(h, "clawvisor").on("maintain", lines=[message(
        "x", commands=["/rollback"], buttons=[[("Go", "ap:v1")]])])
    from talaria import hubmaintain
    hubmaintain.maintain(h, timer=False)
    (m,) = h.ctx.notify.sent
    assert m.commands == ["/rollback clawvisor"] and m.buttons == [[("Go", "clawvisor|ap:v1")]]


# --- cli._locked / run_locked ---------------------------------------------------------------

from argparse import Namespace  # noqa: E402

from talaria import cli, lock  # noqa: E402


@pytest.fixture
def routed(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path, app="gbrain")
    seen = []
    for mod, name in ((cli.check, "check"), (cli.check, "rehearse_tag"),
                      (cli.history, "commit"), (cli.rollback, "rollback_cmd"),
                      (cli.rollback, "restore_cmd"), (cli.maintain, "run")):
        monkeypatch.setattr(mod, name, lambda *a, _n=name: seen.append((_n, a)))
    monkeypatch.setattr(cli.check, "due_today", lambda c: False)
    return ctx, seen


def test_locked_check_without_a_timer_flag_checks(routed):
    ctx, seen = routed
    cli._locked(ctx, Namespace(cmd="check"))
    assert [(n, a[0]) for n, a in seen] == [("check", ctx)]


def test_locked_check_on_a_timer_day_off_only_snapshots(routed):
    ctx, seen = routed
    cli._locked(ctx, Namespace(cmd="check", timer=True))
    assert [(n, a[0], a[2]) for n, a in seen] == [("commit", ctx, "daily")]


def test_locked_passes_the_context_everywhere(routed):
    ctx, seen = routed
    for args in (Namespace(cmd="rehearse", tag="v1"), Namespace(cmd="history"),
                 Namespace(cmd="rollback"), Namespace(cmd="restore", id="b1")):
        cli._locked(ctx, args)
    assert [(n, a[0]) for n, a in seen] == [("rehearse_tag", ctx), ("commit", ctx),
                                            ("rollback_cmd", ctx), ("restore_cmd", ctx)]
    assert seen[1][1][2] == "manual" and seen[3][1][1] == "b1"


def test_run_locked_runs_a_plain_check_of_an_app_with_maintenance(routed):
    ctx, seen = routed
    assert ctx.app.has_maintenance
    assert cli.run_locked(ctx, Namespace(cmd="check", timer=False)) == 0
    assert [n for n, _ in seen] == ["check"]


def test_run_locked_busy_without_a_timer_flag_tells_the_owner(routed, monkeypatch):
    ctx, seen = routed

    def busy(paths):
        raise lock.Busy()
    monkeypatch.setattr(cli.lock, "op_lock", busy)
    assert cli.run_locked(ctx, Namespace(cmd="check")) == cli.EXIT_BUSY
    assert ctx.notify.texts() == ["Busy: another operation is running. Try again in a minute."]
    ctx.notify.sent.clear()
    assert cli.run_locked(ctx, Namespace(cmd="check", timer=True)) == 0
    assert ctx.notify.sent == []

import subprocess

import pytest

from talaria import rehearse
from talaria.shell import Result
from tests.fakes import make_test_ctx
from tests.test_rehearse import IMG, st_with_current
from tests.test_setup import svc  # noqa: F401  (fixture)
from tests.test_setup_service_golden import (NEW, PULLED, PW, RESTART, RUNNING,  # noqa: F401
                                             UNITS, run, s)


def test_initialize_runs_after_the_image_is_tagged_and_before_the_start(s, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(s.app, "initialize",
                        lambda c: (seen.append((c is s, len(c.sh.calls))), ["brain initialized"])[1])
    rc, out, cmds = run(s, capsys)
    assert rc == 0
    assert out == NEW + PW + PULLED + "OK: brain initialized\n" + RUNNING
    assert seen == [(True, len(UNITS))]      # after daemon-reload and podman tag
    assert cmds == UNITS + RESTART


def test_initialize_failure_stops_before_the_start(s, monkeypatch, capsys):
    def boom(c):
        raise ValueError("gbrain init failed: disk full")
    monkeypatch.setattr(s.app, "initialize", boom)
    rc, out, cmds = run(s, capsys)
    assert rc == 1
    assert out == NEW + PW + PULLED + "STOP: gbrain init failed: disk full\n"
    assert cmds == UNITS


def test_setup_notes_follow_the_prepared_lines(s, monkeypatch, capsys):
    monkeypatch.setattr(s.app, "setup_notes", lambda c: ["digest only"] if c is s else [])
    rc, out, _ = run(s, capsys)
    assert rc == 0 and out == NEW + PW + "NOTE: digest only\n" + PULLED + RUNNING


def test_plan_prints_no_notes(s, monkeypatch, capsys):
    monkeypatch.setattr(s.app, "setup_notes", lambda c: ["digest only"])
    rc, out, _ = run(s, capsys, plan=True)
    assert "NOTE" not in out


SVC = "hermes.service"


def rctx(tmp_path, monkeypatch, active="active\n", stopped=True):
    ctx = make_test_ctx(tmp_path)
    (ctx.conf.data_dir / "f").write_text("x")
    ctx.sh.on("systemctl").on("systemctl", "--user", "is-active", out=active)
    monkeypatch.setattr(ctx.app, "copy_stopped", stopped)
    monkeypatch.setattr(ctx.app, "fetch", lambda c, t, commit: dict(IMG))
    seen = []

    def reh(c, st, image, copy, stage):
        seen.append(((copy / "f").read_text(), list(c.sh.calls)))
        return {"tag": image["tag"]}
    monkeypatch.setattr(ctx.app, "rehearse", reh)
    monkeypatch.setattr(ctx.app, "report_lines", lambda c, r: ([], []))
    monkeypatch.setattr(ctx.app, "pending_extra", lambda r: {})
    return ctx, seen


def sc(*a):
    return ["systemctl", "--user", *a, SVC]


def test_copy_stopped_app_is_stopped_only_while_copying(tmp_path, monkeypatch):
    ctx, seen = rctx(tmp_path, monkeypatch)
    rehearse.rehearse(ctx, st_with_current(), "v2026.9.24", "c")
    assert seen == [("x", [sc("is-active"), sc("stop"), sc("reset-failed"), sc("start")])]


def test_an_inactive_app_is_neither_stopped_nor_started(tmp_path, monkeypatch):
    ctx, seen = rctx(tmp_path, monkeypatch, active="inactive\n")
    rehearse.rehearse(ctx, st_with_current(), "v2026.9.24", "c")
    assert seen[0][1] == [sc("is-active")]


def test_apps_without_copy_stopped_copy_while_running(tmp_path, monkeypatch):
    ctx, seen = rctx(tmp_path, monkeypatch, stopped=False)
    rehearse.rehearse(ctx, st_with_current(), "v2026.9.24", "c")
    assert seen[0][1] == []


def test_the_app_is_started_again_when_the_copy_fails(tmp_path, monkeypatch):
    ctx, seen = rctx(tmp_path, monkeypatch)

    def broken(src, dst, excludes):
        raise OSError("disk full")
    monkeypatch.setattr(rehearse, "copy_data", broken)
    with pytest.raises(rehearse.Transient, match="could not copy the data dir: disk full"):
        rehearse.rehearse(ctx, st_with_current(), "v2026.9.24", "c")
    assert ctx.sh.calls == [sc("is-active"), sc("stop"), sc("reset-failed"), sc("start")]
    assert seen == []


@pytest.mark.parametrize("how", ["rc", "timeout"])
def test_the_app_is_started_again_when_the_stop_fails(tmp_path, monkeypatch, how):
    ctx, seen = rctx(tmp_path, monkeypatch)

    def fn(argv, input):
        if how == "timeout":
            raise subprocess.TimeoutExpired(argv, 600)
        return Result(1, "", "Job failed")
    ctx.sh.on("systemctl", "--user", "stop", fn=fn)
    with pytest.raises(rehearse.Transient, match="could not stop"):
        rehearse.rehearse(ctx, st_with_current(), "v2026.9.24", "c")
    assert ctx.sh.calls == [sc("is-active"), sc("stop"), sc("reset-failed"), sc("start")]
    assert seen == []

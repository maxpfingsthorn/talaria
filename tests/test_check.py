import copy

import pytest

from talaria import __version__, check, state
from talaria.rehearse import Permanent, Transient
from talaria.shell import CommandError, Result
from tests.fakes import make_test_ctx


@pytest.fixture
def cctx(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path)
    st = copy.deepcopy(state.DEFAULT)
    st["current"] = {"tag": "v2026.8.3", "id": "sha256:c"}
    ctx.git = {"v2026.8.3": "a", "v2026.9.24": "b"}
    ctx.reg = {"v2026.8.3", "v2026.9.24"}
    ctx.talaria_latest = f"v{__version__}"
    ctx.rehearsed = []
    ctx.rehearse_exc = None
    monkeypatch.setattr(check, "git_release_tags", lambda sh, repo: ctx.git)
    monkeypatch.setattr(check, "registry_tags", lambda sh, image, tls_verify=True: ctx.reg)
    monkeypatch.setattr(check, "latest_semver", lambda sh, repo: ctx.talaria_latest)
    monkeypatch.setattr(check.history, "commit", lambda ctx, st, msg: None)

    def fake_rehearse(ctx_, st_, tag, commit):
        ctx.rehearsed.append((tag, commit))
        if ctx.rehearse_exc:
            raise ctx.rehearse_exc
        st_["pending"] = {"tag": tag}

    monkeypatch.setattr(check, "rehearse", fake_rehearse)
    return ctx, st


def test_new_release_is_rehearsed(cctx):
    ctx, st = cctx
    check.check(ctx, st)
    assert ctx.rehearsed == [("v2026.9.24", "b")]


def test_pending_candidate_not_rehearsed_again(cctx):
    ctx, st = cctx
    st["pending"] = {"tag": "v2026.9.24"}
    check.check(ctx, st)
    assert ctx.rehearsed == []


def test_permanent_failure_marks_failed_and_notifies(cctx):
    ctx, st = cctx
    ctx.rehearse_exc = Permanent("migration failed: boom", ["✗ x"])
    check.check(ctx, st)
    assert st["failed"] == ["v2026.9.24"]
    assert "boom" in ctx.notify.sent[-1].text and ctx.notify.sent[-1].untrusted == ["✗ x"]
    check.check(ctx, st)
    assert len(ctx.rehearsed) == 1          # failed tags are not retried


def test_transient_failure_reported_once(cctx):
    ctx, st = cctx
    ctx.rehearse_exc = Transient("no space")
    check.check(ctx, st)
    check.check(ctx, st)
    assert len(ctx.rehearsed) == 2 and len(ctx.notify.sent) == 1
    assert st["failed"] == []


def test_unreachable_upstream_reported_after_three_days(cctx, monkeypatch):
    ctx, st = cctx

    def down(sh, repo):
        raise CommandError(["git"], Result(128, "", "could not resolve host"))

    monkeypatch.setattr(check, "git_release_tags", down)
    for _ in range(2):
        check.check(ctx, st)
    assert ctx.notify.sent == []
    check.check(ctx, st)
    check.check(ctx, st)
    assert len(ctx.notify.sent) == 1 and "3 days" in ctx.notify.sent[0].text


def test_talaria_update_reminder_once(cctx):
    ctx, st = cctx
    ctx.talaria_latest = "v99.0.0"
    check.check(ctx, st)
    check.check(ctx, st)
    texts = [m.text for m in ctx.notify.sent]
    assert sum("Talaria v99.0.0" in t for t in texts) == 1
    assert st["talaria_notified"] == "v99.0.0"


def test_rehearse_tag_retries_failed(cctx):
    ctx, st = cctx
    st["failed"] = ["v2026.9.24"]
    check.rehearse_tag(ctx, st, "v2026.9.24")
    assert st["failed"] == [] and ctx.rehearsed == [("v2026.9.24", "b")]


def test_rehearse_tag_unknown(cctx):
    ctx, st = cctx
    check.rehearse_tag(ctx, st, "v2030.1.1")
    assert "not a release" in ctx.notify.sent[-1].text

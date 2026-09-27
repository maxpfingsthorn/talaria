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
    monkeypatch.setattr(check, "git_release_tags",
                        lambda sh, repo: (sh is ctx.sh and repo == ctx.conf.hermes_repo) and ctx.git)
    monkeypatch.setattr(check, "registry_tags",
                        lambda sh, image, tls_verify=True: (sh is ctx.sh and image == ctx.conf.image
                                                            and tls_verify is ctx.conf.registry_tls_verify) and ctx.reg)
    monkeypatch.setattr(check, "latest_semver",
                        lambda sh, repo: (sh is ctx.sh and repo == ctx.conf.talaria_repo) and ctx.talaria_latest)
    ctx.history = []
    monkeypatch.setattr(check.history, "commit", lambda c, st, msg: ctx.history.append((c is ctx, msg)))

    def fake_rehearse(ctx_, st_, tag, commit):
        assert ctx_ is ctx
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


# ---- exact behaviour (mutation testing) ----

def test_history_commit_daily(cctx):
    ctx, st = cctx
    check.check(ctx, st)
    assert ctx.history == [(True, "daily")]


def test_failure_counter_exact(cctx, monkeypatch):
    ctx, st = cctx

    def down(sh, repo):
        raise CommandError(["git"], Result(128, "", "no route"))

    monkeypatch.setattr(check, "git_release_tags", down)
    del st["check_failures"]
    for n in range(1, 5):
        check.check(ctx, st)
        assert st["check_failures"] == n
    assert [m.text for m in ctx.notify.sent] == [
        "Talaria could not check for releases for 3 days: git … exited 128: no route"]
    monkeypatch.setattr(check, "git_release_tags", lambda sh, repo: ctx.git)
    check.check(ctx, st)
    assert st["check_failures"] == 0


def test_transient_exact(cctx):
    ctx, st = cctx
    ctx.rehearse_exc = Transient("no space")
    check.check(ctx, st)
    assert st["transient"] == {"tag": "v2026.9.24", "reason": "no space"}
    assert ctx.notify.sent[-1].text == ("Could not rehearse Hermes v2026.9.24: no space. "
                                        "Talaria retries at the next check.")
    ctx.rehearse_exc = Transient("network")
    check.check(ctx, st)
    assert len(ctx.notify.sent) == 2
    ctx.rehearse_exc = None
    check.check(ctx, st)
    assert st["transient"] is None


def test_permanent_text_exact(cctx):
    ctx, st = cctx
    ctx.rehearse_exc = Permanent("boom", ["d"])
    check.check(ctx, st)
    m = ctx.notify.sent[-1]
    assert (m.text, m.untrusted) == ("Hermes v2026.9.24 failed the rehearsal: boom. "
                                     "Production was not touched.", ["d"])


def test_reminder_text_exact(cctx):
    ctx, st = cctx
    ctx.talaria_latest = "v99.0.0"
    check.check(ctx, st)
    assert ctx.notify.sent[0].text == (f"Talaria v99.0.0 is available (installed v{__version__}). "
                                       "Update when convenient: talaria self-update v99.0.0")


def test_reminder_offline_is_silent(cctx, monkeypatch):
    ctx, st = cctx
    monkeypatch.setattr(check, "latest_semver",
                        lambda sh, repo: (_ for _ in ()).throw(CommandError(["git"], Result(1))))
    check.check(ctx, st)
    assert ctx.notify.sent == [] and ctx.rehearsed == [("v2026.9.24", "b")]


def test_reminder_no_release_or_same(cctx):
    ctx, st = cctx
    for latest in (None, f"v{__version__}", "v0.0.1"):
        ctx.talaria_latest = latest
        check.check(ctx, st)
    assert ctx.notify.sent == [] and st["talaria_notified"] is None


def test_first_install_without_current(cctx):
    ctx, st = cctx
    st["current"] = None
    check.check(ctx, st)
    assert ctx.rehearsed == [("v2026.9.24", "b")]


def test_candidate_respects_min_release(cctx):
    ctx, st = cctx
    ctx.conf.min_release = "v2026.10.1"
    check.check(ctx, st)
    assert ctx.rehearsed == []


def test_rejected_not_offered(cctx):
    ctx, st = cctx
    st["rejected"] = ["v2026.9.24"]
    check.check(ctx, st)
    assert ctx.rehearsed == []


def test_rehearse_tag_unknown_text_exact(cctx):
    ctx, st = cctx
    check.rehearse_tag(ctx, st, "v2030.1.1")
    assert ctx.notify.sent[-1].text == ("v2030.1.1 is not a release tag of "
                                        "https://github.com/NousResearch/hermes-agent.")


def test_rehearse_tag_not_failed_before(cctx):
    ctx, st = cctx
    check.rehearse_tag(ctx, st, "v2026.9.24")
    assert ctx.rehearsed == [("v2026.9.24", "b")] and st["failed"] == []

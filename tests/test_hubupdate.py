import pytest

from talaria import __version__, hubupdate, relay
from talaria.hubexec import NoAnswer, Unreachable
from talaria.shell import Result
from tests.hubfakes import ex, hello, make_hub, reply


@pytest.fixture
def h2(tmp_path):
    events = []
    h = make_hub(tmp_path, ("hermes", "clawvisor"), log=events)
    sh = h.ctx.sh
    sh.on("git", fn=lambda argv, input: (events.append(("git", argv[3])), Result(0, "abc1230000000000000000000000000000000000\n"))[1])
    sh.on(str(h.ctx.paths.bin_link), fn=lambda argv, input: (events.append(("setup",)),
                                                             Result(0, "OK: hub units\n"))[1])
    sh.on("systemctl", fn=lambda argv, input: (events.append(("restart",)), Result(0))[1])
    ex(h, "hermes").on("self-update", lines=[reply("Talaria v0.8.0 installed.")])
    ex(h, "clawvisor").on("self-update", lines=[reply("Talaria v0.8.0 installed.")])
    for n in ("hermes", "clawvisor"):       # too old to be pinned, unless a test says otherwise
        ex(h, n).on("hello", lines=[{**hello(app=n), "version": "0.0.1"}])
    h.events = events
    return h


def test_hub_first_then_each_app_then_the_bot(h2, capsys):
    assert hubupdate.self_update(h2, "v0.8.0") == 0
    assert h2.events == [("git", "rev-parse"), ("git", "fetch"), ("git", "rev-parse"), ("git", "checkout"),
                         ("setup",), ("hermes", "call", ("hello",)),
                         ("hermes", "stream", ("self-update", "v0.8.0")),
                         ("clawvisor", "call", ("hello",)),
                         ("clawvisor", "stream", ("self-update", "v0.8.0")), ("restart",)]
    d = str(h2.ctx.paths.install_dir)
    assert list(zip(h2.ctx.sh.calls, h2.ctx.sh.timeouts)) == [
        (["git", "-C", d, "rev-parse", "HEAD"], None),
        (["git", "-C", d, "fetch", "-q", "origin", "+refs/tags/v0.8.0:refs/tags/v0.8.0"], 600),
        (["git", "-C", d, "rev-parse", "--verify", "-q", "refs/tags/v0.8.0^{commit}"], None),
        (["git", "-C", d, "checkout", "-q", "v0.8.0"], None),
        ([str(h2.ctx.paths.bin_link), "setup", "--as-hub", "--no-restart"], 600),
        (["systemctl", "--user", "restart", "talaria-telegram.service"], None)]
    assert h2.ctx.notify.texts() == ["Talaria v0.8.0 installed (commit abc123000000): Hermes ✓ · Clawvisor ✓"]
    assert capsys.readouterr().out == "OK: hub units\n"


def test_partial_failure_keeps_going_and_names_the_reason(h2):
    ex(h2, "clawvisor").on("self-update", lines=[
        reply("self-update failed (exit 1); details in the journal")], rc=1)
    ex(h2, "hermes").on("self-update", exc=Unreachable("hermes"))
    assert hubupdate.self_update(h2, "v0.8.0") == 1
    assert h2.ctx.notify.texts() == [
        "Talaria v0.8.0 installed (commit abc123000000): Hermes ✗ (sudo rule missing) · "
        "Clawvisor ✗ (self-update failed (exit 1); details in the journal)"]
    assert h2.events[-1] == ("restart",)


def test_app_failure_without_a_reply_names_the_exit_code(h2):
    ex(h2, "clawvisor").on("self-update", rc=75)
    hubupdate.self_update(h2, "v0.8.0")
    assert h2.ctx.notify.texts() == ["Talaria v0.8.0 installed (commit abc123000000): Hermes ✓ · Clawvisor ✗ (exit 75)"]


def test_hub_checkout_failure_changes_nothing(h2, capsys):
    h2.ctx.sh.on("git", "-C", str(h2.ctx.paths.install_dir), "checkout", rc=1, err="no such ref")
    assert hubupdate.self_update(h2, "v0.8.0") == 1
    (text,) = h2.ctx.notify.texts()
    assert text.startswith("Talaria v0.8.0 was not installed: the hub could not check it out (")
    assert text.endswith("). Nothing changed.") and "no such ref" in text
    assert not any(e[0] in ("hermes", "clawvisor", "restart") for e in h2.events)
    assert text in capsys.readouterr().err


def test_hub_setup_failure_stops_before_the_apps(h2):
    h2.ctx.sh.on(str(h2.ctx.paths.bin_link), rc=1, out="STOP: x\n")
    assert hubupdate.self_update(h2, "v0.8.0") == 1
    assert h2.ctx.notify.texts() == ["Talaria v0.8.0: the hub's setup failed (exit 1); the "
                                     "previous version was restored (its setup failed too) and "
                                     "the apps were not updated. Details in the journal."]
    assert not any(e[0] in ("hermes", "clawvisor", "restart") for e in h2.events)


@pytest.mark.parametrize("second_fails", [False, True])
@pytest.mark.parametrize("first", ["rc", "timeout"])
def test_hub_failed_setup_rerenders(h2, first, second_fails):
    import subprocess
    it = iter([Result(1, "STOP: x\n") if first == "rc" else subprocess.TimeoutExpired("x", 600),
               Result(1) if second_fails else Result(0)])

    def fn(argv, input):
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    h2.ctx.sh.on(str(h2.ctx.paths.bin_link), fn=fn)
    assert hubupdate.self_update(h2, "v0.8.0") == 1
    d = str(h2.ctx.paths.install_dir)
    setup = [str(h2.ctx.paths.bin_link), "setup", "--as-hub", "--no-restart"]
    assert h2.ctx.sh.calls[-2:] == [["git", "-C", d, "checkout", "-q", "abc1230000000000000000000000000000000000"], setup]
    (text,) = h2.ctx.notify.texts()
    assert "the previous version was restored" in text
    assert ("its setup failed too" in text) == second_fails
    assert not any(e[0] in ("hermes", "clawvisor", "restart") for e in h2.events)


def test_transitional_update_skips_the_hub_install(tmp_path):
    events = []
    h = make_hub(tmp_path, ("hermes",), transitional=True, log=events)
    h.ctx.sh.on("systemctl", fn=lambda argv, input: (events.append(("restart",)), Result(0))[1])
    h.ctx.sh.on("git", rc=1)        # no such tag in the hub's install: nothing to pin
    ex(h, "hermes").on("self-update", lines=[reply("Talaria v0.8.0 installed.")])
    ex(h, "hermes").on("hello", lines=[{**hello(), "version": "0.0.1"}])
    assert hubupdate.self_update(h, "v0.8.0") == 0
    assert events == [("hermes", "call", ("hello",)), ("hermes", "stream", ("self-update", "v0.8.0")),
                      ("restart",)]
    assert h.ctx.notify.texts() == ["Talaria v0.8.0 installed: Hermes ✓"]


def test_would_restart(tmp_path):
    h = make_hub(tmp_path)
    e = h.apps["hermes"]
    ex(h, "hermes").on("self-update", lines=[dict(reply("would restart: yes"), restart=True)])
    assert hubupdate.would_restart(e, "v0.8.0") == "yes"
    ex(h, "hermes").on("self-update", lines=[reply("dry run failed: x")], rc=1)
    assert hubupdate.would_restart(e, "v0.8.0") == "unknown"
    ex(h, "hermes").on("self-update", exc=Unreachable("hermes"))
    assert hubupdate.would_restart(e, "v0.8.0") == "unknown (sudo rule missing)"
    ex(h, "hermes").on("self-update", exc=NoAnswer("hermes"))
    assert hubupdate.would_restart(e, "v0.8.0") == "unknown (no answer)"


def test_would_restart_untrusted(tmp_path):
    h = make_hub(tmp_path)
    ex(h, "hermes").on("self-update", lines=[dict(reply("not signed by a trusted key: x"),
                                                  trusted=False)], rc=1)
    assert hubupdate.would_restart(h.apps["hermes"], "v0.8.0") == (
        "unknown (not signed by a trusted key)")


def test_offer_text_and_button(tmp_path):
    h = make_hub(tmp_path)
    ex(h, "hermes").on("self-update", lines=[dict(reply("would restart: no"), restart=False)])
    assert hubupdate.offer(h, "v0.8.0") == 0
    (m,) = h.ctx.notify.sent
    assert m.text == f"Talaria v0.8.0 is available (installed v{__version__}). Hermes would restart: no"
    assert m.buttons == [[("Update Talaria to v0.8.0", "hub|up:v0.8.0")]]


def test_start_update_runs_in_a_transient_unit(tmp_path, monkeypatch, capsys):
    h = make_hub(tmp_path)
    h.ctx.sh.on("systemd-run")
    monkeypatch.setattr(hubupdate.time, "time", lambda: 1234.5)
    assert hubupdate.start_update(h, "v0.8.0") == 0
    assert h.ctx.sh.calls == [["systemd-run", "--user", "--collect", "--quiet",
                               "--unit=talaria-update-1234", str(h.ctx.paths.bin_link),
                               "self-update", "v0.8.0"]]
    assert capsys.readouterr().out == ("Updating Talaria to v0.8.0 in the background; the bot "
                                       "reports the result.\n")


def test_retry_updates_only_lagging_apps(h2):
    tag = f"v{__version__}"
    ex(h2, "hermes").on("hello", lines=[hello(app="hermes")])
    ex(h2, "clawvisor").on("hello", lines=[{**hello(app="clawvisor"), "version": "0.0.1"}])
    assert hubupdate.self_update(h2, tag) == 0
    assert not [e for e in h2.events if e[0] == "setup" or e[:2] == ("git", "checkout")]
    assert [e for e in h2.events if e[1:2] == ("stream",)] == [
        ("clawvisor", "stream", ("self-update", tag))]
    assert h2.ctx.notify.texts() == [f"Talaria {tag} installed (commit abc123000000): Clawvisor ✓"]


def test_update_drives_old_app_protocol(h2):
    ex(h2, "hermes").on("self-update", lines=[reply("Talaria v0.8.0 installed.")])
    assert hubupdate._update_app(h2.apps["hermes"], "v0.8.0", None) == (True, "Hermes ✓")
    assert [c[1] for c in ex(h2, "hermes").calls] == [["hello"], ["self-update", "v0.8.0"]]


def test_retry_with_no_lagging_app_says_so_and_restarts_nothing(h2):
    tag = f"v{__version__}"
    for n in ("hermes", "clawvisor"):
        ex(h2, n).on("hello", lines=[hello(app=n)])
    assert hubupdate.self_update(h2, tag) == 0
    assert h2.ctx.notify.texts() == [f"Talaria {tag}: no app lags behind; nothing to do."]
    assert ["systemctl", "--user", "restart", "talaria-telegram.service"] not in h2.ctx.sh.calls


def test_offer_for_the_installed_version_names_the_retry(tmp_path):
    h = make_hub(tmp_path)
    ex(h, "hermes").on("hello", lines=[{**hello(), "version": "0.0.1"}])
    ex(h, "hermes").on("self-update", lines=[dict(reply("would restart: no"), restart=False)])
    hubupdate.offer(h, f"v{__version__}")
    assert h.ctx.notify.sent[0].text.startswith(f"Retrying Talaria v{__version__} for apps that lag.")


def test_retry_skips_an_app_ahead_of_the_hub(h2, monkeypatch):
    monkeypatch.setattr(relay, "__version__", "0.7.3")
    monkeypatch.setattr(hubupdate, "__version__", "0.7.3")
    ex(h2, "hermes").on("hello", lines=[hello(version="0.7.4")])
    ex(h2, "clawvisor").on("hello", lines=[hello(app="clawvisor", version="0.7.2")])
    ex(h2, "clawvisor").on("self-update", lines=[reply("Talaria v0.7.3 installed.")])
    hubupdate.offer(h2, "v0.7.3")
    (text,) = h2.ctx.notify.texts()
    assert "Hermes" not in text and "Clawvisor would restart" in text
    assert hubupdate.self_update(h2, "v0.7.3") == 0
    assert ex(h2, "hermes").ops() == [["hello"]] * 2
    assert ["self-update", "v0.7.3"] in ex(h2, "clawvisor").ops()


SHA = "0123456789abcdef0123456789abcdef01234567"


def test_update_sends_commit_only_to_apps_that_know_it(h2):
    h2.ctx.sh.on("git", "-C", str(h2.ctx.paths.install_dir), "rev-parse", "--verify", out=SHA + "\n")
    ex(h2, "hermes").on("hello", lines=[{**hello(app="hermes"), "version": "0.7.2"}])
    ex(h2, "clawvisor").on("hello", lines=[{**hello(app="clawvisor"), "version": "0.7.3"}])
    assert hubupdate.self_update(h2, "v0.8.0") == 0
    streams = lambda n: [a for how, a, _ in ex(h2, n).calls if how == "stream"]
    assert streams("hermes") == [["self-update", "v0.8.0"]]
    assert streams("clawvisor") == [["self-update", "v0.8.0", "--commit", SHA]]
    assert h2.ctx.notify.texts() == [f"Talaria v0.8.0 installed (commit {SHA[:12]}): Hermes ✓ · Clawvisor ✓"]


def test_app_with_unknown_version_gets_no_commit(h2):
    h2.ctx.sh.on("git", "-C", str(h2.ctx.paths.install_dir), "rev-parse", "--verify", out=SHA + "\n")
    for n in ("hermes", "clawvisor"):
        ex(h2, n).on("hello", exc=Unreachable(n))        # version unknown
    assert hubupdate.self_update(h2, "v0.8.0") == 0
    assert all("--commit" not in a for n in ("hermes", "clawvisor")
               for how, a, _ in ex(h2, n).calls if how == "stream")


def test_retry_pins_the_commit_of_the_hubs_own_tag(h2):
    tag = f"v{__version__}"
    h2.ctx.sh.on("git", out=SHA + "\n")
    ex(h2, "clawvisor").on("hello", lines=[hello(app="clawvisor")])      # up to date
    ex(h2, "hermes").on("hello", lines=[{**hello(app="hermes"), "version": "0.0.1"}])
    assert hubupdate.self_update(h2, tag) == 0
    d = str(h2.ctx.paths.install_dir)
    assert h2.ctx.sh.calls[0] == ["git", "-C", d, "rev-parse", "--verify", "-q",
                                  f"refs/tags/{tag}^{{commit}}"]
    assert h2.ctx.notify.texts() == [f"Talaria {tag} installed (commit {SHA[:12]}): Hermes ✓"]


def test_retry_without_the_tag_on_the_hub_sends_no_commit(h2):
    tag = f"v{__version__}"
    h2.ctx.sh.on("git", rc=1)
    ex(h2, "clawvisor").on("hello", lines=[{**hello(app="clawvisor"), "version": "0.0.1"}])
    assert hubupdate.self_update(h2, tag) == 0
    assert all("--commit" not in a for how, a, _ in ex(h2, "clawvisor").calls if how == "stream")


def test_a_normal_update_skips_an_app_already_ahead_of_the_tag(h2):
    ex(h2, "clawvisor").on("hello", lines=[{**hello(app="clawvisor"), "version": "0.9.0"}])
    assert hubupdate.self_update(h2, "v0.8.0") == 0
    assert ("clawvisor", "stream", ("self-update", "v0.8.0")) not in h2.events
    assert h2.ctx.notify.texts()[-1].endswith("Hermes ✓ · Clawvisor skipped (already on v0.9.0)")


def test_an_odd_commit_id_is_not_sent_as_a_pin(h2):
    h2.ctx.sh.on("git", fn=lambda argv, input: Result(0, "not-a-sha\n"))
    ex(h2, "hermes").on("hello", lines=[{**hello(), "version": "0.7.3"}])
    assert hubupdate.self_update(h2, "v0.8.0") == 0
    assert ("hermes", "stream", ("self-update", "v0.8.0")) in h2.events
    assert "(commit" not in h2.ctx.notify.texts()[-1]

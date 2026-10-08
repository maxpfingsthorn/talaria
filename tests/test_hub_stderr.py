from talaria import hubupdate, relay
from talaria.hubexec import SudoExecutor, last_line
from tests.hubfakes import ex, make_hub, message


def test_last_line_trims_and_skips_blanks():
    assert last_line("a\nValueError: boom\n\n  \n") == "ValueError: boom"
    assert last_line("") == ""
    assert len(last_line("x" * 500)) == 200


def test_sudo_executor_keeps_the_last_stderr_line(tmp_path):
    sh = tmp_path / "fake"
    sh.write_text("#!/bin/sh\necho 'Traceback' >&2\necho 'ValueError: unknown key x' >&2\nexit 1\n")
    sh.chmod(0o755)
    e = SudoExecutor("hermes", "u", tmp_path, sudo=str(sh))
    e.call(["hello"])
    assert (e.returncode, e.stderr_line) == (1, "ValueError: unknown key x")
    list(e.stream(["x"]))
    assert e.stderr_line == "ValueError: unknown key x"


def test_update_report_shows_why_an_app_failed(tmp_path):
    h = make_hub(tmp_path, ("hermes", "clawvisor"))
    ex(h, "hermes").on("self-update")
    ex(h, "clawvisor").on("self-update", rc=1, err="ValueError: unknown key in c: x")
    h.ctx.sh.on("systemctl")
    h.transitional = True       # skip the hub's own checkout
    sent = []
    h.ctx.notify.send = sent.append
    assert hubupdate.self_update(h, "v0.5.3") == 1
    assert "Hermes ✓ · Clawvisor ✗ (exit 1: ValueError: unknown key in c: x)" in sent[-1].text


def test_relay_failure_carries_the_stderr_line(tmp_path):
    h = make_hub(tmp_path)
    ex(h, "hermes").on("status", rc=1, err="ValueError: bad")
    sent = []
    h.ctx.notify.send = sent.append
    assert relay.relay_sent(h, "hermes", ["status"]) == (1, False)
    assert sent[0].text == "Hermes: status failed unexpectedly (exit 1): ValueError: bad"
    assert relay.quick(h.apps["hermes"], ["status"])[0] == sent[0].text


def test_failure_without_stderr_keeps_the_short_text(tmp_path):
    h = make_hub(tmp_path)
    ex(h, "hermes").on("status", rc=3)
    assert relay.quick(h.apps["hermes"], ["status"])[0] == \
        "Hermes: status failed unexpectedly (exit 3)"


def test_hello_crash_reports_the_line_and_counts_as_mismatch(tmp_path):
    h = make_hub(tmp_path)
    ex(h, "hermes").on("hello", rc=1, err="ValueError: unknown key")
    t = relay.hello(h.apps["hermes"])
    assert t.startswith(relay.VERSIONS) and "(exit 1): ValueError: unknown key" in t

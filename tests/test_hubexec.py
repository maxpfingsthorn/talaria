# tests/test_hubexec.py
import os
import threading
from types import SimpleNamespace

import pytest

from talaria import hubexec
from talaria.ctx import Paths
from talaria.hubconf import HubConf
from talaria.hubexec import LocalExecutor, NoAnswer, SudoExecutor, Unreachable, load_hub


def script(tmp_path, body, name="talaria"):
    p = tmp_path / name
    p.write_text("#!/bin/bash\n" + body + "\n")
    p.chmod(0o755)
    return p


def test_parse_lines_keeps_protocol_objects_and_logs_the_rest(capsys):
    text = ('{"v": 1, "kind": "reply", "text": "hi"}\n\n{"v": 2, "kind": "reply"}\n'
            '[1]\nnot json\n{"v": 1}\n')
    assert hubexec.parse_lines(text, "hermes") == [{"v": 1, "kind": "reply", "text": "hi"}]
    err = capsys.readouterr().err.splitlines()
    assert err == ['[talaria] hermes: ignored output: {"v": 2, "kind": "reply"}',
                   "[talaria] hermes: ignored output: [1]",
                   "[talaria] hermes: ignored output: not json",
                   '[talaria] hermes: ignored output: {"v": 1}']


def test_call_returns_lines_and_exit_code(tmp_path, capsys):
    s = script(tmp_path, """echo '{"v": 1, "kind": "reply", "text": "hi"}'; echo noise
echo oops >&2; exit 3""")
    e = LocalExecutor("hermes", s)
    assert e.call(["status"]) == [{"v": 1, "kind": "reply", "text": "hi"}]
    assert e.returncode == 3
    err = capsys.readouterr().err
    assert "oops" in err and "[talaria] hermes: ignored output: noise" in err


def test_local_executor_argv_and_arguments(tmp_path):
    s = script(tmp_path, """printf '{"v": 1, "kind": "reply", "text": "%s"}\\n' "$*" """)
    e = LocalExecutor("clawvisor", s)
    assert e.argv(["button", "ap:v1"]) == [str(s), "op", "button", "ap:v1"]
    assert e.call(["button", "ap:v1"])[0]["text"] == "op button ap:v1"


def test_executor_runs_from_root(tmp_path, monkeypatch):
    s = script(tmp_path, """printf '{"v": 1, "kind": "reply", "text": "%s"}\\n' "$PWD" """)
    monkeypatch.chdir(tmp_path)
    assert LocalExecutor("hermes", s).call(["status"])[0]["text"] == "/"
    assert [d["text"] for d in LocalExecutor("hermes", s).stream(["status"])] == ["/"]


def test_call_timeout_is_no_answer(tmp_path):
    s = script(tmp_path, "sleep 5")
    with pytest.raises(NoAnswer):
        LocalExecutor("hermes", s).call(["status"], timeout=0.3)


def test_stream_yields_each_line_then_sets_the_exit_code(tmp_path):
    s = script(tmp_path, """echo '{"v": 1, "kind": "message", "text": "a"}'
echo '{"v": 1, "kind": "message", "text": "b"}'; exit 1""")
    e = LocalExecutor("hermes", s)
    got = []
    for d in e.stream(["deploy", "v1"]):
        got.append(d["text"])
        assert e.returncode is None or got == ["a", "b"]
    assert got == ["a", "b"] and e.returncode == 1


def test_stream_survives_lots_of_stderr(tmp_path):
    s = script(tmp_path, """head -c 2000000 /dev/zero | tr '\\0' x >&2
echo '{"v": 1, "kind": "message", "text": "done"}'""")
    got = []
    t = threading.Thread(target=lambda: got.extend(LocalExecutor("hermes", s).stream(["check"])))
    t.start()
    t.join(30)
    assert not t.is_alive(), "relay deadlocked on stderr"
    assert [d["text"] for d in got] == ["done"]


def test_sudo_executor_argv():
    e = SudoExecutor("hermes", "hermes", "/home/hermes")
    assert e.argv(["status"]) == ["sudo", "-n", "-H", "-u", "hermes",
                                  "/home/hermes/.local/bin/talaria", "op", "status"]


def test_sudo_refusal_is_unreachable(tmp_path):
    fake = script(tmp_path, 'echo "sudo: a password is required" >&2; exit 1', name="sudo")
    e = SudoExecutor("hermes", "hermes", "/home/hermes", sudo=str(fake))
    with pytest.raises(Unreachable):
        e.call(["status"])
    with pytest.raises(Unreachable):
        list(e.stream(["deploy", "v1"]))


def test_op_failure_is_not_a_sudo_refusal(tmp_path):
    fake = script(tmp_path, 'echo "Traceback (most recent call last):" >&2; exit 1', name="sudo")
    e = SudoExecutor("hermes", "hermes", "/home/hermes", sudo=str(fake))
    assert e.call(["status"]) == [] and e.returncode == 1
    assert list(e.stream(["status"])) == [] and e.returncode == 1


# ---- load_hub ----

def pw(home):
    return SimpleNamespace(pw_dir=home)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_no_conf_is_no_hub(tmp_path):
    assert load_hub(tmp_path) is None


def test_app_under_a_hub_is_no_hub(tmp_path):
    write(Paths(tmp_path).conf_file, "app = hermes\n")
    assert load_hub(tmp_path) is None


def test_hub(tmp_path):
    p = Paths(tmp_path)
    write(p.hub_conf, "apps = hermes:hermes clawvisor:cv\n")
    write(p.env_file, "TALARIA_TELEGRAM_TOKEN=1:abc\nTALARIA_TELEGRAM_USER_ID=42\n")
    homes = {"hermes": "/home/hermes", "cv": "/srv/cv"}
    h = load_hub(tmp_path, getpwnam=lambda u: pw(homes[u]), sh="SH")
    assert h.transitional is False and isinstance(h.ctx.conf, HubConf)
    assert h.ctx.sh == "SH" and h.ctx.paths.home == tmp_path and h.ctx.app is None
    assert list(h.apps) == ["hermes", "clawvisor"]
    cv = h.apps["clawvisor"]
    assert (cv.name, cv.title) == ("clawvisor", "Clawvisor")
    assert cv.executor.argv(["hello"]) == ["sudo", "-n", "-H", "-u", "cv",
                                           "/srv/cv/.local/bin/talaria", "op", "hello"]


def test_hub_conf_wins_over_an_own_token(tmp_path):
    p = Paths(tmp_path)
    write(p.hub_conf, "")
    write(p.conf_file, "app = hermes\n")
    write(p.env_file, "TALARIA_TELEGRAM_TOKEN=1:abc\nTALARIA_TELEGRAM_USER_ID=42\n")
    h = load_hub(tmp_path)
    assert h.transitional is False and h.apps == {}


def test_hub_with_a_vanished_account(tmp_path):
    write(Paths(tmp_path).hub_conf, "apps = hermes:gone\n")

    def getpwnam(u):
        raise KeyError(u)
    with pytest.raises(ValueError, match="registers hermes for gone, but that account does not exist"):
        load_hub(tmp_path, getpwnam=getpwnam)


def test_transitional_v04_install(tmp_path):
    p = Paths(tmp_path)
    write(p.conf_file, "app = clawvisor\n")
    write(p.env_file, "TALARIA_TELEGRAM_TOKEN=1:abc\nTALARIA_TELEGRAM_USER_ID=42\n")
    h = load_hub(tmp_path)
    assert h.transitional is True
    assert h.ctx.app.name == "clawvisor" and h.ctx.paths.app == "clawvisor"
    assert h.ctx.conf.telegram_user_id == 42
    (e,) = h.apps.values()
    assert (e.name, e.title) == ("clawvisor", "Clawvisor")
    assert e.executor.argv(["hello"]) == [str(tmp_path / ".local/bin/talaria"), "op", "hello"]


def test_token_without_owner_is_no_hub(tmp_path):
    p = Paths(tmp_path)
    write(p.conf_file, "app = hermes\n")
    write(p.env_file, "TALARIA_TELEGRAM_TOKEN=1:abc\n")
    assert load_hub(tmp_path) is None

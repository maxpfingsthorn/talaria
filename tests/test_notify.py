import pytest
import json

from talaria.conf import Conf
from talaria.notify import ApiError, Message, TelegramNotifier, render


def test_render_escapes_and_wraps_untrusted():
    m = Message("Hermes <v2> & more", untrusted=["<b>x</b> /rollback"],
                commands=["/approve v2026.8.3"])
    out = render(m)
    assert out.startswith("Hermes &lt;v2&gt; &amp; more")
    assert "<pre>&lt;b&gt;x&lt;/b&gt; /rollback</pre>" in out
    assert "<code>/approve v2026.8.3</code>" in out


def test_render_truncates_to_limit():
    m = Message("head", untrusted=["x" * 10000, "y" * 10000], commands=["/a"])
    out = render(m)
    assert len(out) <= 4096
    assert out.count("<pre>") == out.count("</pre>") == 2
    assert "truncated" in out and "<code>/a</code>" in out


class FakeAPI:
    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        if self.failures:
            raise self.failures.pop(0)
        return {"message_id": 1}


def conf():
    return Conf(data_dir=None, telegram_token="t", telegram_user_id=42)


def test_send_posts_html_to_owner():
    api = FakeAPI([])
    TelegramNotifier(conf(), api=api, sleep=lambda s: None).send(Message("hi"))
    method, params = api.calls[0]
    assert method == "sendMessage"
    assert params["chat_id"] == 42 and params["parse_mode"] == "HTML"


def test_send_retries_429_with_retry_after():
    slept = []
    api = FakeAPI([ApiError(429, 7), ApiError(502, None)])
    TelegramNotifier(conf(), api=api, sleep=slept.append).send(Message("hi"))
    assert len(api.calls) == 3 and slept[0] == 7


def test_send_never_raises(capsys):
    api = FakeAPI([ApiError(500, None)] * 5)
    TelegramNotifier(conf(), api=api, sleep=lambda s: None).send(Message("lost"))
    assert len(api.calls) == 3
    assert "lost" in capsys.readouterr().err


def test_send_without_token_only_logs(capsys):
    n = TelegramNotifier(Conf(data_dir=None), api=FakeAPI([]), sleep=lambda s: None)
    n.send(Message("x"))
    assert "x" in capsys.readouterr().err


def test_api_passes_timeout_to_telegram_and_waits_longer(monkeypatch):
    import io
    import urllib.request
    from talaria.notify import TelegramAPI
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        seen["timeout"], seen["body"] = timeout, json.loads(req.data)
        return Resp(b'{"ok": true, "result": []}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    api = TelegramAPI("http://x", "t")
    assert api.call("getUpdates", offset=-1, timeout=0) == []
    assert seen["body"] == {"offset": -1, "timeout": 0} and seen["timeout"] >= 10
    api.call("getUpdates", offset=5, timeout=30)
    assert seen["body"]["timeout"] == 30 and seen["timeout"] > 30


# ---- exact behaviour (mutation testing) ----

import io
import urllib.error
import urllib.request

from talaria.notify import TelegramAPI


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture(monkeypatch, response=b'{"ok": true, "result": {"x": 1}}', exc=None):
    seen = {}

    def fake(req, timeout):
        seen.update(url=req.full_url, data=json.loads(req.data), timeout=timeout,
                    ctype=req.get_header("Content-type"), method=req.get_method())
        if exc:
            raise exc
        return _Resp(response)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return seen


def test_api_request_exact(monkeypatch):
    seen = _capture(monkeypatch)
    assert TelegramAPI("http://h/", "T0K").call("sendMessage", chat_id=1, text="x") == {"x": 1}
    assert seen == {"url": "http://h/botT0K/sendMessage", "data": {"chat_id": 1, "text": "x"},
                    "timeout": 15.0, "ctype": "application/json", "method": "POST"}


def test_api_http_error_with_retry_after(monkeypatch):
    body = io.BytesIO(b'{"ok": false, "parameters": {"retry_after": 9}}')
    _capture(monkeypatch, exc=urllib.error.HTTPError("u", 429, "slow", {}, body))
    with pytest.raises(ApiError) as e:
        TelegramAPI("http://h", "t").call("getMe")
    assert (e.value.status, e.value.retry_after) == (429, 9)
    assert str(e.value) == "telegram api status 429"


def test_api_http_error_without_body(monkeypatch):
    _capture(monkeypatch, exc=urllib.error.HTTPError("u", 502, "bad", {}, io.BytesIO(b"<html>")))
    with pytest.raises(ApiError) as e:
        TelegramAPI("http://h", "t").call("getMe")
    assert (e.value.status, e.value.retry_after) == (502, None)


def test_api_http_error_body_without_parameters(monkeypatch):
    _capture(monkeypatch, exc=urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"ok": false}')))
    with pytest.raises(ApiError) as e:
        TelegramAPI("http://h", "t").call("getMe")
    assert (e.value.status, e.value.retry_after) == (400, None)


@pytest.mark.parametrize("exc", [urllib.error.URLError("down"), OSError("reset")])
def test_api_network_errors_are_status_0(monkeypatch, exc):
    _capture(monkeypatch, exc=exc)
    with pytest.raises(ApiError) as e:
        TelegramAPI("http://h", "t").call("getMe")
    assert (e.value.status, e.value.retry_after) == (0, None)


def test_api_bad_json_is_status_0(monkeypatch):
    _capture(monkeypatch, response=b"not json")
    with pytest.raises(ApiError) as e:
        TelegramAPI("http://h", "t").call("getMe")
    assert e.value.status == 0


def test_api_not_ok_is_400(monkeypatch):
    _capture(monkeypatch, response=b'{"ok": false, "description": "nope"}')
    with pytest.raises(ApiError) as e:
        TelegramAPI("http://h", "t").call("getMe")
    assert e.value.status == 400


def test_send_exact_params_and_log(capsys):
    api = FakeAPI([])
    m = Message("hi <b>", untrusted=["u1", "u2"], commands=["/a"])
    TelegramNotifier(conf(), api=api, sleep=lambda s: None).send(m)
    assert api.calls == [("sendMessage", {"chat_id": 42, "text": render(m), "parse_mode": "HTML",
                                          "disable_web_page_preview": True})]
    assert capsys.readouterr().err == "[talaria] message: hi <b>\nu1\nu2\n"


def test_send_retry_sleeps_exact():
    slept = []
    api = FakeAPI([ApiError(500, None), ApiError(0, None), ApiError(502, None)])
    TelegramNotifier(conf(), api=api, sleep=slept.append).send(Message("x"))
    assert slept == [1, 2, 4] and len(api.calls) == 3


def test_send_429_without_retry_after_backs_off():
    slept = []
    api = FakeAPI([ApiError(429, None)])
    TelegramNotifier(conf(), api=api, sleep=slept.append).send(Message("x"))
    assert slept == [1] and len(api.calls) == 2


def test_send_permanent_4xx_gives_up_at_once(capsys):
    slept = []
    api = FakeAPI([ApiError(400, None), ApiError(400, None)])
    TelegramNotifier(conf(), api=api, sleep=slept.append).send(Message("x"))
    assert len(api.calls) == 1 and slept == []
    assert capsys.readouterr().err.endswith(
        "[talaria] telegram delivery failed; message above is in the journal only\n")


def test_send_success_logs_no_failure(capsys):
    TelegramNotifier(conf(), api=FakeAPI([]), sleep=lambda s: None).send(Message("x"))
    assert "delivery failed" not in capsys.readouterr().err


def test_send_without_user_id_only_logs():
    api = FakeAPI([])
    TelegramNotifier(Conf(data_dir=None, telegram_token="t"), api=api,
                     sleep=lambda s: None).send(Message("x"))
    assert api.calls == []


def test_notifier_builds_api_from_conf(monkeypatch):
    seen = _capture(monkeypatch, response=b'{"ok": true, "result": {}}')
    c = Conf(data_dir=None, telegram_token="TT", telegram_user_id=5, telegram_api="http://api")
    TelegramNotifier(c, sleep=lambda s: None).send(Message("x"))
    assert seen["url"] == "http://api/botTT/sendMessage"


def test_render_exact_layout():
    m = Message("T", untrusted=["a"], commands=["/x", "/y"])
    assert render(m) == "T\n\n<pre>a</pre>\n\n<code>/x</code> · <code>/y</code>"
    assert render(Message("T")) == "T"


def test_render_long_text_without_untrusted_is_cut():
    assert len(render(Message("x" * 5000))) == 4096


def test_render_short_untrusted_kept_whole():
    m = Message("head", untrusted=["short", "y" * 9000])
    out = render(m)
    assert "<pre>short</pre>" in out and len(out) <= 4096


def test_render_escaping_growth_still_fits():
    m = Message("h", untrusted=['"' * 5000])
    out = render(m)
    assert len(out) <= 4096 and out.endswith("</pre>")
    assert "truncated, full text in the journal" in out


# ---- v0.2: inline buttons ----

def test_send_attaches_inline_keyboard():
    api = FakeAPI([])
    m = Message("x", buttons=[[("Approve v2", "ap:v2"), ("Reject", "rj:v2")]])
    TelegramNotifier(conf(), api=api, sleep=lambda s: None).send(m)
    assert api.calls[0][1]["reply_markup"] == {"inline_keyboard": [
        [{"text": "Approve v2", "callback_data": "ap:v2"},
         {"text": "Reject", "callback_data": "rj:v2"}]]}


def test_send_without_buttons_has_no_markup():
    api = FakeAPI([])
    TelegramNotifier(conf(), api=api, sleep=lambda s: None).send(Message("x"))
    assert "reply_markup" not in api.calls[0][1]


def test_keyboard_drops_buttons_over_64_bytes():
    from talaria.notify import keyboard
    assert keyboard([[("ok", "a" * 64), ("too long", "b" * 65)], [("x", "c" * 70)]]) == {
        "inline_keyboard": [[{"text": "ok", "callback_data": "a" * 64}]]}
    assert keyboard([]) is None


# ---- titled untrusted blocks ----

def test_render_titled_blocks():
    m = Message("T", untrusted=[("Migrations <2>", "a\nb"), "plain"])
    assert render(m) == "T\n\n<b>Migrations &lt;2&gt;</b>\n<pre>a\nb</pre>\n\n<pre>plain</pre>"


def test_render_titled_blocks_truncate_within_limit():
    m = Message("T", untrusted=[("Big", "x" * 9000), ("Also big", "y" * 9000)])
    out = render(m)
    assert len(out) <= 4096 and out.count("<pre>") == 2 and "<b>Also big</b>" in out



# ---- v0.2.3: flaky networks ----

def test_network_error_names_its_cause(monkeypatch):
    _capture(monkeypatch, exc=urllib.error.URLError("timed out"))
    with pytest.raises(ApiError) as e:
        TelegramAPI("http://h", "t").call("getUpdates", offset=1, timeout=25)
    assert str(e.value) == "telegram api status 0: <urlopen error timed out>"


def test_http_timeout_is_poll_plus_5_at_least_15(monkeypatch):
    seen = _capture(monkeypatch, response=b'{"ok": true, "result": []}')
    TelegramAPI("http://h", "t").call("getUpdates", offset=1, timeout=25)
    assert seen["timeout"] == 30
    TelegramAPI("http://h", "t").call("getMe")
    assert seen["timeout"] == 15

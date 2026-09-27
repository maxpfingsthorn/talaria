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

    def call(self, method, timeout=35, **params):
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

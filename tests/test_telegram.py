import pytest

from talaria import state, telegram
from talaria.notify import ApiError
from tests.fakes import make_test_ctx

OWNER = 42


class FakeAPI:
    def __init__(self, ctx, batches=()):
        self.ctx, self.batches, self.calls = ctx, list(batches), []

    def call(self, method, **params):     # same signature as TelegramAPI.call
        self.calls.append((method, params))
        if method == "getUpdates":
            timeout = params.get("timeout", 0)
            if timeout > 0:
                self.ctx.clock.sleep(timeout)
            if len(self.calls) > 1000:
                raise AssertionError("runaway polling loop")
            return self.batches.pop(0) if self.batches else []
        return {"message_id": 1}

    def sent(self):
        return [p["text"] for m, p in self.calls if m == "sendMessage"]


def upd(uid, text, user=OWNER, chat_type="private"):
    return {"update_id": uid, "message": {"text": text, "chat": {"id": user, "type": chat_type},
                                          "from": {"id": user, "first_name": "Ann",
                                                   "username": "ann"}}}


@pytest.fixture
def bot(tmp_path):
    ctx = make_test_ctx(tmp_path, telegram_user_id=OWNER, telegram_token="t")
    ctx.sh.on("systemd-run").on("systemctl", "--user", "is-active", out="active\n")
    api = FakeAPI(ctx)
    b = telegram.Bot(ctx, api)
    b.offset = 1
    return ctx, api, b


def test_new_code():
    c = telegram.new_code()
    assert len(c) == 8 and c.isalnum() and not set(c) & set("0O1IL")


def test_only_owner_in_private_chat(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/status", user=7))
    b.handle(upd(2, "/status", chat_type="group"))
    assert api.sent() == []
    b.handle(upd(3, "/status"))
    assert "Hermes" in api.sent()[0]


def test_approve_spawns_deploy_with_absolute_path(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/approve v2026.9.24"))
    argv = ctx.sh.called("systemd-run")[0]
    assert argv[:4] == ["systemd-run", "--user", "--collect", "--quiet"]
    assert argv[-3:] == [str(ctx.paths.bin_link), "deploy", "v2026.9.24"]
    assert ctx.paths.bin_link.is_absolute()


def test_invalid_arguments_never_spawn(bot):
    ctx, api, b = bot
    for text in ["/approve v2026.9.24;rm -rf", "/approve", "/restore ../x CONFIRM",
                 "/rollback confirm", "/approve $(id)"]:
        b.handle(upd(1, text))
    assert ctx.sh.called("systemd-run") == []


def test_rollback_describe_only_without_confirm(bot, monkeypatch):
    ctx, api, b = bot
    monkeypatch.setattr(telegram.rollback, "describe", lambda c: "would restore")
    b.handle(upd(1, "/rollback"))
    assert api.sent() == ["would restore"] and ctx.sh.called("systemd-run") == []
    b.handle(upd(2, "/rollback CONFIRM"))
    assert ctx.sh.called("systemd-run")[0][-2:] == ["rollback", "--confirm"]


def test_restore_confirm(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/restore 20260927T043000Z-manual CONFIRM"))
    assert ctx.sh.called("systemd-run")[0][-3:] == ["restore", "20260927T043000Z-manual",
                                                    "--confirm"]


def test_reject_in_process(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/reject v2026.9.24"))
    assert state.load(ctx.paths)["rejected"] == ["v2026.9.24"]


def test_command_with_bot_suffix(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/check@talaria_bot"))
    assert ctx.sh.called("systemd-run")[0][-1] == "check"


def test_startup_drops_backlog_and_reports_interruption(bot):
    ctx, api, b = bot
    st = state.load(ctx.paths)
    st["op"] = {"op": "deploy", "tag": "v2026.9.24", "started": ctx.now().isoformat()}
    state.save(ctx.paths, st)
    api.batches = [[upd(10, "/approve v2026.9.24")], []]
    b.startup()
    assert b.offset == 11 and ctx.sh.called("systemd-run") == []
    assert "Interrupted" in ctx.notify.sent[-1].text


def test_poll_advances_offset(bot):
    ctx, api, b = bot
    api.batches = [[upd(5, "/check"), upd(6, "/status")]]
    b.poll_once()
    assert b.offset == 7


def test_pair_first_correct_sender_wins(tmp_path):
    ctx = make_test_ctx(tmp_path)
    api = FakeAPI(ctx, [[upd(1, "old")], [upd(2, "/pair WRONG", user=9), upd(3, "/pair ABCD2345",
                                                                           user=77)]])
    who = telegram.pair(ctx, api, "ABCD2345")
    assert who["id"] == 77


def test_pair_ignores_groups_and_expires(tmp_path):
    ctx = make_test_ctx(tmp_path)
    api = FakeAPI(ctx, [[], [upd(2, "/pair ABCD2345", chat_type="group")]])
    assert telegram.pair(ctx, api, "ABCD2345", timeout_s=60) is None

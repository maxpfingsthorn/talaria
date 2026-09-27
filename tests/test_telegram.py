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
    api = FakeAPI(ctx, [[upd(1, "old")], [], [upd(2, "/pair WRONG", user=9), upd(3, "/pair ABCD2345",
                                                                           user=77)]])
    who = telegram.pair(ctx, api, "ABCD2345")
    assert who["id"] == 77


def test_pair_ignores_groups_and_expires(tmp_path):
    ctx = make_test_ctx(tmp_path)
    api = FakeAPI(ctx, [[], [upd(2, "/pair ABCD2345", chat_type="group")]])
    assert telegram.pair(ctx, api, "ABCD2345", timeout_s=60) is None


def test_pair_code_shown_only_after_backlog_is_dropped(tmp_path):
    ctx = make_test_ctx(tmp_path)
    api = FakeAPI(ctx, [[upd(1, "old")]])
    # the person answers instantly: the message exists before the next poll
    announce = lambda: api.batches.append([upd(2, "/pair ABCD2345", user=77)])
    who = telegram.pair(ctx, api, "ABCD2345", announce=announce)
    assert who["id"] == 77


# ---- exact behaviour (mutation testing) ----

class Stop(Exception):
    pass


def test_run_requires_token_and_user(tmp_path, capsys):
    ctx = make_test_ctx(tmp_path, telegram_token="t")
    assert telegram.run(ctx) == 1
    assert capsys.readouterr().err == "talaria bot: token or user id missing; run talaria setup\n"
    ctx = make_test_ctx(tmp_path, telegram_user_id=5)
    assert telegram.run(ctx) == 1


def test_run_backs_off_and_resets(tmp_path, monkeypatch, capsys):
    ctx = make_test_ctx(tmp_path, telegram_user_id=OWNER, telegram_token="t")
    ctx.sh.on("systemctl", "--user", "is-active", out="active\n")
    events = [ApiError(0, None)] * 8 + [None, ApiError(502, None), Stop()]
    made = []

    class LoopAPI:
        def __init__(self, base, token):
            made.append((base, token))

        def call(self, method, **p):
            if p.get("offset") == -1 or p.get("timeout") == 0:
                return []
            e = events.pop(0)
            if e:
                raise e
            return []

    slept = []
    monkeypatch.setattr(telegram, "TelegramAPI", LoopAPI)
    monkeypatch.setattr(telegram.time, "sleep", slept.append)
    with pytest.raises(Stop):
        telegram.run(ctx)
    assert made == [(ctx.conf.telegram_api, "t")]
    assert slept == [1, 2, 4, 8, 16, 32, 60, 60, 1]
    assert "[talaria] telegram: telegram api status 0" in capsys.readouterr().err


def test_run_startup_once(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path, telegram_user_id=OWNER, telegram_token="t")
    starts = []
    monkeypatch.setattr(telegram.Bot, "startup", lambda self: starts.append(1))
    polls = [None, None, Stop()]

    def poll(self):
        p = polls.pop(0)
        if p:
            raise p

    monkeypatch.setattr(telegram.Bot, "poll_once", poll)
    monkeypatch.setattr(telegram, "TelegramAPI", lambda b, t: None)
    with pytest.raises(Stop):
        telegram.run(ctx)
    assert starts == [1]


def test_reply_exact(bot):
    ctx, api, b = bot
    b.reply("x" * 5000)
    method, params = api.calls[-1]
    assert method == "sendMessage" and params == {"chat_id": OWNER, "text": "x" * 4096}


def test_spawn_exact(bot, monkeypatch):
    ctx, api, b = bot
    monkeypatch.setattr(telegram.time, "time", lambda: 1234.9)
    b.spawn("deploy", "v2026.9.24")
    assert ctx.sh.calls[-1] == ["systemd-run", "--user", "--collect", "--quiet",
                                "--unit=talaria-op-deploy-1234", str(ctx.paths.bin_link),
                                "deploy", "v2026.9.24"]
    assert ctx.sh.timeouts[-1] is None


@pytest.mark.parametrize("text,reply", [
    ("/check", "Checking for releases."),
    ("/approve v2026.9.24", "Deploying v2026.9.24. I will report the result."),
    ("/rollback CONFIRM", "Rolling back. I will report the result."),
    ("/restore 20260927T043000Z-manual CONFIRM",
     "Restoring 20260927T043000Z-manual. I will report the result."),
    ("/help", "Not understood. Commands: " + telegram.HELP),
    ("/status extra", "Not understood. Commands: " + telegram.HELP),
    ("/backups x", "Not understood. Commands: " + telegram.HELP),
    ("/check now", "Not understood. Commands: " + telegram.HELP),
    ("/reject", "Not understood. Commands: " + telegram.HELP),
    ("/reject v1", "Not understood. Commands: " + telegram.HELP),
    ("/rollback CONFIRM now", "Not understood. Commands: " + telegram.HELP),
    ("/restore 20260927T043000Z-manual confirm", "Not understood. Commands: " + telegram.HELP),
    ("/restore", "Not understood. Commands: " + telegram.HELP),
])
def test_dispatch_replies_exact(bot, text, reply):
    ctx, api, b = bot
    b.handle(upd(1, text))
    assert api.sent() == [reply]


def test_help_text_exact():
    assert telegram.HELP == ("/status · /check · /approve <tag> · /reject <tag> · "
                             "/rollback [CONFIRM] · /backups · /restore <id> [CONFIRM]")


def test_backups_command(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/backups"))
    assert api.sent() == ["No backups yet."]


def test_restore_describe(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/restore 20260927T043000Z-manual"))
    assert api.sent() == ["No backup 20260927T043000Z-manual. /backups lists them."]


def test_dispatch_error_is_replied(bot, monkeypatch):
    ctx, api, b = bot
    monkeypatch.setattr(telegram.status, "status_text", lambda c: 1 / 0)
    b.handle(upd(1, "/status"))
    assert api.sent() == ["Error: division by zero"]


def test_empty_or_missing_text_is_ignored(bot, capsys):
    ctx, api, b = bot
    b.handle(upd(1, "   "))
    b.handle({"update_id": 2, "message": {"chat": {"type": "private"}, "from": {"id": OWNER}}})
    b.handle({"update_id": 3})
    assert api.sent() == []
    assert capsys.readouterr().err == ("[talaria] ignored update 1\n[talaria] ignored update 2\n"
                                       "[talaria] ignored update 3\n")


def test_poll_once_params_exact(bot):
    ctx, api, b = bot
    b.offset = 7
    b.poll_once()
    assert api.calls[-1] == ("getUpdates", {"offset": 7, "timeout": 30,
                                            "allowed_updates": ["message"]})


def test_startup_without_backlog(bot):
    ctx, api, b = bot
    api.batches = [[]]
    b.startup()
    assert b.offset is None and api.calls == [("getUpdates", {"offset": -1, "timeout": 0})]
    assert ctx.notify.sent == []


def test_startup_ack_exact(bot):
    ctx, api, b = bot
    api.batches = [[upd(4, "x")], []]
    b.startup()
    assert api.calls == [("getUpdates", {"offset": -1, "timeout": 0}),
                         ("getUpdates", {"offset": 5, "timeout": 0})]


def test_pair_exact_calls(tmp_path):
    ctx = make_test_ctx(tmp_path)
    api = FakeAPI(ctx, [[upd(1, "old")], [], [upd(2, "/pair ABCD2345", user=77)], []])
    announced = []
    who = telegram.pair(ctx, api, "ABCD2345", announce=lambda: announced.append(len(api.calls)))
    assert who == {"id": 77, "first_name": "Ann", "username": "ann"}
    assert announced == [2]
    assert api.calls == [("getUpdates", {"offset": -1, "timeout": 0}),
                         ("getUpdates", {"offset": 2, "timeout": 0}),
                         ("getUpdates", {"offset": 2, "timeout": 30}),
                         ("getUpdates", {"offset": 3, "timeout": 0}),
                         ("sendMessage", {"chat_id": 77,
                                          "text": "Paired. This chat now controls Talaria."})]


def test_pair_without_backlog_polls_from_start(tmp_path):
    ctx = make_test_ctx(tmp_path)
    api = FakeAPI(ctx, [[], [upd(1, "/pair ABCD2345")], []])
    assert telegram.pair(ctx, api, "ABCD2345")["id"] == OWNER
    assert api.calls[1] == ("getUpdates", {"offset": None, "timeout": 30})


def test_pair_code_must_match_exactly(tmp_path):
    ctx = make_test_ctx(tmp_path)
    api = FakeAPI(ctx, [[], [upd(1, "/pair ABCD2345 x"), upd(2, "/pair abcd2345"),
                             upd(3, "pair ABCD2345")]])
    assert telegram.pair(ctx, api, "ABCD2345", timeout_s=60) is None


def test_pair_default_timeout_is_15_minutes(tmp_path):
    ctx = make_test_ctx(tmp_path)
    api = FakeAPI(ctx, [])
    telegram.pair(ctx, api, "ABCD2345")
    assert ctx.clock.slept == 900


def test_new_code_alphabet():
    assert telegram.ALPHABET == "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    codes = {telegram.new_code() for _ in range(200)}
    assert len(codes) > 190 and all(set(c) <= set(telegram.ALPHABET) for c in codes)

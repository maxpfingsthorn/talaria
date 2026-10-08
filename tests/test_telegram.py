# tests/test_telegram.py
import pytest

from talaria import relay, telegram
from talaria.hubexec import NoAnswer, Unreachable
from talaria.notify import ApiError
from tests.fakes import make_test_ctx
from tests.hubfakes import ex, hello, line, make_hub, reply

OWNER = 42
ID = "20260927T043000Z-manual"
NU = "Not understood. Commands: " + telegram.HELP


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


def cb(data, user=OWNER, chat_type="private", mid=55):
    return {"update_id": 9, "callback_query": {
        "id": "q1", "data": data, "from": {"id": user},
        "message": {"message_id": mid, "chat": {"id": user, "type": chat_type}}}}


def status_markup(label):
    return {"chat_id": OWNER, "message_id": 55,
            "reply_markup": {"inline_keyboard": [[{"text": label, "callback_data": "done"}]]}}


def calls(api, method):
    return [p for m, p in api.calls if m == method]


def spawned(ctx):
    return [c[5:] for c in ctx.sh.called("systemd-run")]


def _bot(tmp_path, names):
    hub = make_hub(tmp_path, names)
    hub.ctx.sh.on("systemd-run")
    for n in names:
        ex(hub, n).on("hello", lines=[hello(app=n)]).on("interrupted").on(
            "status", lines=[reply(f"{hub.apps[n].title} v1, running.")])
    api = FakeAPI(hub.ctx)
    b = telegram.Bot(hub, api)
    b.offset = 1
    return hub.ctx, api, b


@pytest.fixture
def bot(tmp_path):
    return _bot(tmp_path, ("hermes",))


@pytest.fixture
def bot2(tmp_path):
    return _bot(tmp_path, ("hermes", "clawvisor"))


def test_new_code():
    c = telegram.new_code()
    assert len(c) == 8 and c.isalnum() and not set(c) & set("0O1IL")


def test_only_owner_in_private_chat(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/status", user=7))
    b.handle(upd(2, "/status", chat_type="group"))
    assert api.sent() == []
    b.handle(upd(3, "/status"))
    assert api.sent() == ["Hermes v1, running."]


def test_status_lists_every_app(bot2):
    ctx, api, b = bot2
    b.handle(upd(1, "/status"))
    assert api.sent() == ["Hermes v1, running.\n\nClawvisor v1, running."]


def test_status_of_one_app(bot2):
    ctx, api, b = bot2
    b.handle(upd(1, "/status clawvisor"))
    assert api.sent() == ["Clawvisor v1, running."]


def test_app_name_is_optional_with_one_app(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/approve v2026.9.24"))
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "relay", "hermes", "deploy", "v2026.9.24"]]
    assert api.sent() == ["Deploying Hermes v2026.9.24. I will report the result."]


def test_spawn_exact(bot, monkeypatch):
    ctx, api, b = bot
    monkeypatch.setattr(telegram.time, "time", lambda: 1234.9)
    b.spawn("hermes-deploy", "relay", "hermes", "deploy", "v2026.9.24")
    assert ctx.sh.calls[-1] == ["systemd-run", "--user", "--collect", "--quiet",
                                "--unit=talaria-hermes-deploy-1234", str(ctx.paths.bin_link),
                                "relay", "hermes", "deploy", "v2026.9.24"]
    assert ctx.sh.timeouts[-1] is None and ctx.paths.bin_link.is_absolute()


@pytest.mark.parametrize("text,reply_text,run", [
    ("/check clawvisor", "Checking Clawvisor for releases.", ["relay", "clawvisor", "check"]),
    ("/approve clawvisor v0.9.10", "Deploying Clawvisor v0.9.10. I will report the result.",
     ["relay", "clawvisor", "deploy", "v0.9.10"]),
    ("/rollback hermes CONFIRM", "Rolling back Hermes. I will report the result.",
     ["relay", "hermes", "rollback", "confirm"]),
    (f"/restore hermes {ID} CONFIRM", f"Restoring Hermes {ID}. I will report the result.",
     ["relay", "hermes", "restore", ID, "confirm"]),
    ("/check@talaria_bot hermes", "Checking Hermes for releases.", ["relay", "hermes", "check"]),
])
def test_long_commands_spawn_the_relay(bot2, text, reply_text, run):
    ctx, api, b = bot2
    b.handle(upd(1, text))
    assert api.sent() == [reply_text]
    assert spawned(ctx) == [[str(ctx.paths.bin_link), *run]]
    unit = ctx.sh.called("systemd-run")[0][4]
    assert unit.startswith(f"--unit=talaria-{run[1]}-{run[2]}-")


def test_quick_commands_ask_the_app(bot2):
    ctx, api, b = bot2
    ex(b.hub, "hermes").on("reject", lines=[reply("Rejected v2026.9.24. It will not be offered again.")])
    ex(b.hub, "hermes").on("rollback", lines=[reply("would restore", [[("Roll back", "rb:B:1")]])])
    ex(b.hub, "clawvisor").on("backups", lines=[reply("No backups yet.")])
    ex(b.hub, "clawvisor").on("restore", lines=[reply(f"No backup {ID}. /backups lists them.")])
    for text in ("/reject hermes v2026.9.24", "/rollback hermes", "/backups clawvisor",
                 f"/restore clawvisor {ID}"):
        b.handle(upd(1, text))
    assert ex(b.hub, "hermes").ops()[-2:] == [["reject", "v2026.9.24"], ["rollback", "describe"]]
    assert ex(b.hub, "clawvisor").ops()[-2:] == [["backups"], ["restore", ID, "describe"]]
    rb = calls(api, "sendMessage")[1]
    assert rb["text"] == "would restore"
    assert rb["reply_markup"] == {"inline_keyboard": [[{"text": "Roll back",
                                                        "callback_data": "hermes|rb:B:1"}]]}
    assert spawned(ctx) == []


@pytest.mark.parametrize("text,form", [
    ("/backups", "backups"), ("/approve v2026.9.24", "status"),
    ("/reject v0.9.10", "status"), ("/rollback", "rollback"), ("/rollback CONFIRM", "rollback"),
    (f"/restore {ID}", f"restore:{ID}"), (f"/restore {ID} CONFIRM", f"restore:{ID}"),
])
def test_which_app(bot2, text, form):
    ctx, api, b = bot2
    b.handle(upd(1, text))
    (params,) = calls(api, "sendMessage")
    assert params["text"] == "Which app?"
    assert params["reply_markup"] == {"inline_keyboard": [[
        {"text": "Hermes", "callback_data": f"hub|w:hermes:{form}"},
        {"text": "Clawvisor", "callback_data": f"hub|w:clawvisor:{form}"}]]}
    assert spawned(ctx) == []
    assert all(o in (["hello"], ["interrupted"]) for n in b.hub.apps for o in ex(b.hub, n).ops())


def test_which_app_restore_button_fits():
    assert len(f"hub|w:clawvisor:restore:20261231T235959Z-pre-v2026.12.31.99-2".encode()) <= 64
    assert telegram.read_form("/restore", ["20261231T235959Z-pre-v2026.12.31.99-2"]) == \
        "restore:20261231T235959Z-pre-v2026.12.31.99-2"


def test_which_app_button_runs_the_read_form_never_a_confirm(bot2):
    ctx, api, b = bot2
    ex(b.hub, "hermes").on("rollback", lines=[reply("would restore", [[("Roll back", "rb:B:1")]])])
    ex(b.hub, "clawvisor").on("restore", lines=[reply("Restore it?")])
    b.handle(cb("hub|w:hermes:rollback"))
    b.handle(cb(f"hub|w:clawvisor:restore:{ID}"))
    assert ex(b.hub, "hermes").ops()[-1] == ["rollback", "describe"]
    assert ex(b.hub, "clawvisor").ops()[-1] == ["restore", ID, "describe"]
    assert api.sent() == ["would restore", "Restore it?"]
    assert spawned(ctx) == []
    assert [p["text"] for p in calls(api, "answerCallbackQuery")] == ["Hermes", "Clawvisor"]
    assert calls(api, "editMessageReplyMarkup") == []


@pytest.mark.parametrize("data", ["hub|w:nope:status", "hub|w:hermes:deploy", "hub|w:hermes:check", "hub|w:hermes:restore:../x",
                                  "hub|w:hermes:status:x", "hub|x", "hub|up:latest"])
def test_bad_hub_buttons(bot2, data):
    ctx, api, b = bot2
    b.handle(cb(data))
    assert calls(api, "answerCallbackQuery")[0]["text"] == "Unknown button"
    assert spawned(ctx) == [] and api.sent() == []


def test_unknown_app(bot2):
    ctx, api, b = bot2
    b.handle(upd(1, "/backups nope"))
    assert api.sent() == ["Unknown app: nope. Apps: hermes, clawvisor"]


def test_no_apps_registered(tmp_path):
    ctx, api, b = _bot(tmp_path, ())
    b.handle(upd(1, "/backups"))
    b.handle(upd(2, "/status"))
    assert api.sent() == ["No apps registered.", "No apps registered."]


@pytest.mark.parametrize("text", [
    "/help", "/status hermes extra", "/backups hermes x", "/check hermes now", "/reject",
    "/reject v1", "/approve v0.9.10", "/rollback CONFIRM now", f"/restore {ID} confirm",
    "/restore", "/update", "/update latest", "/update v1.2", "/approve $(id)",
])
def test_not_understood(bot, text):
    ctx, api, b = bot
    b.handle(upd(1, text))
    assert api.sent() == [NU] and spawned(ctx) == []


def test_invalid_arguments_never_spawn(bot):
    ctx, api, b = bot
    for text in ["/approve v2026.9.24;rm -rf", "/approve", "/restore ../x CONFIRM",
                 "/rollback confirm", "/approve $(id)", "/update v1.2.3;id"]:
        b.handle(upd(1, text))
    assert spawned(ctx) == []


def test_help_text_exact():
    assert telegram.HELP == ("/status · /check [app|talaria] · /approve [app] <tag> · /reject [app] <tag> · "
                             "/rollback [app] [CONFIRM] · /backups [app] · "
                             "/restore [app] <id> [CONFIRM] · /update <version>")


def test_update_command_spawns_the_offer(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/update v0.6.0"))
    assert api.sent() == ["Checking what Talaria v0.6.0 would change. I will send the result."]
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "update", "v0.6.0", "--offer"]]
    assert ctx.sh.called("systemd-run")[0][4].startswith("--unit=talaria-update-")


def test_quick_errors_name_the_app(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("status", exc=Unreachable("hermes"))
    b.handle(upd(1, "/status"))
    ex(b.hub, "hermes").on("backups", exc=NoAnswer("hermes"))
    b.handle(upd(2, "/backups"))
    assert api.sent() == [relay.unreachable_text("Hermes"), "Hermes did not answer in time"]


def test_versions_differ(bot2):
    ctx, api, b = bot2
    b.mismatch = {"clawvisor"}
    for text in ("/backups clawvisor", "/approve clawvisor v0.9.10", "/status"):
        b.handle(upd(1, text))
    assert api.sent() == [relay.VERSIONS, relay.VERSIONS,
                          "Hermes v1, running.\n\nClawvisor: " + relay.VERSIONS]
    b.handle(cb("clawvisor|ap:v0.9.10"))
    assert calls(api, "answerCallbackQuery")[0]["text"] == relay.VERSIONS
    b.handle(upd(2, "/update v0.6.0"))
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "update", "v0.6.0", "--offer"]]
    assert ["button", "ap:v0.9.10"] not in ex(b.hub, "clawvisor").ops()


def test_dispatch_error_is_replied(bot, monkeypatch):
    ctx, api, b = bot
    monkeypatch.setattr(telegram.relay, "status_all", lambda h, m: 1 / 0)
    b.handle(upd(1, "/status"))
    assert api.sent() == ["Error: division by zero"]


def test_empty_answer_sends_nothing(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("backups")            # replied nothing, exit 0
    b.handle(upd(1, "/backups"))
    assert api.sent() == []


# ---- startup and polling ----

def test_startup_checks_versions_and_reports_interruptions(bot2):
    ctx, api, b = bot2
    ex(b.hub, "hermes").on("interrupted", lines=[reply("Interrupted deploy v2026.9.24 (3m ago).")])
    ex(b.hub, "clawvisor").on("hello", lines=[hello(protocol=2, app="clawvisor")])
    api.batches = [[upd(10, "/approve v2026.9.24")], []]
    b.startup()
    assert b.offset == 11 and spawned(ctx) == []
    assert b.mismatch == {"clawvisor"}
    assert ctx.notify.texts() == ["Interrupted deploy v2026.9.24 (3m ago)."]
    assert ["interrupted"] not in ex(b.hub, "clawvisor").ops()


def test_startup_marks_a_failing_hello_as_mismatch(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("hello", rc=2)
    b.startup()
    assert b.mismatch == {"hermes"}


def test_startup_reports_an_unreachable_app(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("hello", exc=Unreachable("hermes")).on(
        "interrupted", exc=Unreachable("hermes"))
    b.startup()
    assert b.mismatch == set() and ctx.notify.texts() == [relay.unreachable_text("Hermes")]


def test_startup_registers_the_app_neutral_menu_for_the_owner_only(bot):
    ctx, api, b = bot
    b.startup()
    (params,) = calls(api, "setMyCommands")
    assert params["scope"] == {"type": "chat", "chat_id": OWNER}
    assert [(c["command"], c["description"]) for c in params["commands"]] == telegram.MENU
    assert [c for c, _ in telegram.MENU] == ["status", "check", "approve", "reject", "rollback",
                                             "backups", "restore", "update"]
    assert all(0 < len(d) <= 256 and "Hermes" not in d for _, d in telegram.MENU)


def test_menu_failure_does_not_stop_startup(bot):
    ctx, api, b = bot
    real = api.call

    def call(method, **p):
        if method == "setMyCommands":
            raise ApiError(400, None)
        return real(method, **p)

    api.call = call
    b.startup()          # no exception


def test_startup_without_backlog(bot):
    ctx, api, b = bot
    api.batches = [[]]
    b.startup()
    assert b.offset is None and [c for c in api.calls if c[0] == "getUpdates"] == [
        ("getUpdates", {"offset": -1, "timeout": 0})]
    assert ctx.notify.sent == []


def test_startup_ack_exact(bot):
    ctx, api, b = bot
    api.batches = [[upd(4, "x")], []]
    b.startup()
    assert [c for c in api.calls if c[0] == "getUpdates"] == [
        ("getUpdates", {"offset": -1, "timeout": 0}), ("getUpdates", {"offset": 5, "timeout": 0})]


def test_poll_advances_offset(bot):
    ctx, api, b = bot
    api.batches = [[upd(5, "/check"), upd(6, "/status")]]
    b.poll_once()
    assert b.offset == 7


def test_poll_once_params_exact(bot):
    ctx, api, b = bot
    b.offset = 7
    b.poll_once()
    assert api.calls[-1] == ("getUpdates", {"offset": 7, "timeout": 25,
                                            "allowed_updates": ["message", "callback_query"]})


def test_reply_exact(bot):
    ctx, api, b = bot
    b.reply("x" * 5000)
    method, params = api.calls[-1]
    assert method == "sendMessage" and params == {"chat_id": OWNER, "text": "x" * 4096}


def test_empty_or_missing_text_is_ignored(bot, capsys):
    ctx, api, b = bot
    b.handle(upd(1, "   "))
    b.handle({"update_id": 2, "message": {"chat": {"type": "private"}, "from": {"id": OWNER}}})
    b.handle({"update_id": 3})
    assert api.sent() == []
    assert capsys.readouterr().err == ("[talaria] ignored update 1\n[talaria] ignored update 2\n"
                                       "[talaria] ignored update 3\n")


# ---- buttons ----

def test_prefixed_button_asks_the_app_and_runs_its_answer(bot2):
    ctx, api, b = bot2
    ex(b.hub, "clawvisor").on("button", lines=[line(
        "button", toast="Deploying", status="✅ Approved — deploying v0.9.10",
        run=["deploy", "v0.9.10"])])
    b.handle(cb("clawvisor|ap:v0.9.10"))
    assert ex(b.hub, "clawvisor").calls[-1] == ("call", ["button", "ap:v0.9.10"], 60)
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "relay", "clawvisor", "deploy", "v0.9.10"]]
    assert calls(api, "answerCallbackQuery") == [{"callback_query_id": "q1", "text": "Deploying"}]
    assert calls(api, "editMessageReplyMarkup") == [status_markup("✅ Approved — deploying v0.9.10")]
    assert api.sent() == []


def test_button_without_run_only_answers(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("button", lines=[line("button", toast="Rejected",
                                                 status="❌ Rejected v2026.9.24", run=None)])
    b.handle(cb("hermes|rj:v2026.9.24"))
    assert spawned(ctx) == []
    assert calls(api, "editMessageReplyMarkup") == [status_markup("❌ Rejected v2026.9.24")]


@pytest.mark.parametrize("run", [["setup"], ["deploy", 1], [], "deploy", None])
def test_button_runs_only_long_ops(bot, run):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("button", lines=[line("button", toast="x", status=None, run=run)])
    b.handle(cb("hermes|ap:v2026.9.24"))
    assert spawned(ctx) == []


@pytest.mark.parametrize("data", ["ap:v2026.9.24", "nope|ap:v1", "|ap:v1"])
def test_unprefixed_or_unknown_app_buttons_are_out_of_date(bot, data):
    ctx, api, b = bot
    b.handle(cb(data))
    assert calls(api, "answerCallbackQuery")[0]["text"] == "Out of date — send /status"
    assert calls(api, "editMessageReplyMarkup") == [status_markup("⌛ Out of date")]
    assert ["button"] not in [o[:1] for o in ex(b.hub, "hermes").ops()]


def test_app_answer_without_a_button_line(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("button", rc=2)
    b.handle(cb("hermes|zz"))
    assert calls(api, "answerCallbackQuery")[0]["text"] == "Unknown button"


def test_button_for_an_unreachable_app(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("button", exc=Unreachable("hermes"))
    b.handle(cb("hermes|ap:v2026.9.24"))
    assert calls(api, "answerCallbackQuery")[0]["text"] == relay.unreachable_text("Hermes")
    assert calls(api, "editMessageReplyMarkup") == []


def test_update_button(bot):
    ctx, api, b = bot
    b.handle(cb("hub|up:v0.6.0"))
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "self-update", "v0.6.0"]]
    assert calls(api, "answerCallbackQuery")[0]["text"] == "Updating"
    assert calls(api, "editMessageReplyMarkup") == [status_markup("⬆️ Updating Talaria to v0.6.0…")]


def test_status_button_tap_does_nothing(bot):
    ctx, api, b = bot
    b.handle(cb("done"))
    assert calls(api, "answerCallbackQuery") == [{"callback_query_id": "q1", "text": "Already handled"}]
    assert calls(api, "editMessageReplyMarkup") == [] and spawned(ctx) == []


@pytest.mark.parametrize("update", [cb("hermes|ap:v2026.9.24", user=7),
                                    cb("hermes|ap:v2026.9.24", chat_type="group")])
def test_buttons_from_others_are_ignored(bot, update):
    ctx, api, b = bot
    b.handle(update)
    assert spawned(ctx) == [] and api.calls == []


@pytest.mark.parametrize("failing", ["answerCallbackQuery", "editMessageReplyMarkup"])
def test_button_handling_survives_api_errors(bot, failing, capsys):
    # a late tap makes Telegram answer 400 ("query is too old"); the rest must still happen
    ctx, api, b = bot
    ex(b.hub, "hermes").on("button", lines=[line("button", toast="Deploying", status="✅",
                                                 run=["deploy", "v2026.9.24"])])
    real = api.call

    def call(method, **p):
        if method == failing:
            api.calls.append((method, p))
            raise ApiError(400, None)
        return real(method, **p)

    api.call = call
    b.handle(cb("hermes|ap:v2026.9.24"))
    assert spawned(ctx)[0][-2:] == ["deploy", "v2026.9.24"]
    assert [m for m, _ in api.calls if m == "editMessageReplyMarkup"]
    assert f"[talaria] telegram {failing}: telegram api status 400" in capsys.readouterr().err


def test_poll_asks_for_callback_queries(bot):
    ctx, api, b = bot
    b.poll_once()
    assert calls(api, "getUpdates")[-1]["allowed_updates"] == ["message", "callback_query"]


# ---- pairing (unchanged) ----

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
    announce = lambda: api.batches.append([upd(2, "/pair ABCD2345", user=77)])
    who = telegram.pair(ctx, api, "ABCD2345", announce=announce)
    assert who["id"] == 77


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


# ---- run ----

class Stop(Exception):
    pass


def test_run_requires_token_and_user(tmp_path, capsys):
    hub = make_hub(tmp_path)
    hub.ctx.conf.telegram_user_id = 0
    assert telegram.run(hub) == 1
    assert capsys.readouterr().err == "talaria bot: token or user id missing; run talaria setup\n"
    hub.ctx.conf.telegram_user_id, hub.ctx.conf.telegram_token = 5, ""
    assert telegram.run(hub) == 1


def test_run_backs_off_and_resets(tmp_path, monkeypatch, capsys):
    hub = make_hub(tmp_path)
    ex(hub, "hermes").on("hello", lines=[hello()]).on("interrupted")
    events = [ApiError(0, None)] * 8 + [None, ApiError(502, None), Stop()]
    made = []

    class LoopAPI:
        def __init__(self, base, token):
            made.append((base, token))

        def call(self, method, **p):
            if method == "setMyCommands" or p.get("offset") == -1 or p.get("timeout") == 0:
                return []
            e = events.pop(0)
            if e:
                raise e
            return []

    slept = []
    monkeypatch.setattr(telegram, "TelegramAPI", LoopAPI)
    monkeypatch.setattr(telegram.time, "sleep", slept.append)
    with pytest.raises(Stop):
        telegram.run(hub)
    assert made == [(hub.ctx.conf.telegram_api, "t")]
    assert slept == [1, 2, 4, 8, 16, 32, 60]      # first failure retries at once
    assert "[talaria] telegram: telegram api status 0" in capsys.readouterr().err


def test_run_startup_once(tmp_path, monkeypatch):
    hub = make_hub(tmp_path)
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
        telegram.run(hub)
    assert starts == [1]


def test_bare_check_runs_the_whole_hub_check(bot2):
    ctx, api, b = bot2
    b.handle(upd(1, "/check"))
    assert api.sent() == ["Checking Hermes, Clawvisor and Talaria."]
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "check", "--report"]]
    assert ctx.sh.called("systemd-run")[0][4].startswith("--unit=talaria-check-")
    assert all(o in (["hello"], ["interrupted"]) for n in b.hub.apps for o in ex(b.hub, n).ops())


def test_bare_check_with_one_app_does_not_ask_which(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/check"))
    assert api.sent() == ["Checking Hermes and Talaria."]
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "check", "--report"]]


def test_check_talaria_checks_only_talaria(bot2):
    ctx, api, b = bot2
    b.handle(upd(1, "/check talaria"))
    assert api.sent() == ["Checking for a new Talaria release."]
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "check", "--talaria"]]
    assert ctx.sh.called("systemd-run")[0][4].startswith("--unit=talaria-talaria-check-")


def test_check_talaria_with_extra_words_is_not_understood(bot2):
    ctx, api, b = bot2
    b.handle(upd(1, "/check talaria now"))
    assert api.sent()[0].startswith("Not understood.") and spawned(ctx) == []


def test_bare_check_never_shows_the_picker(bot2):
    ctx, api, b = bot2
    b.handle(upd(1, "/check"))
    assert "Which app?" not in api.sent() and telegram.read_form("/check", []) is None

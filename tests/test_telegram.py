# tests/test_telegram.py
import pytest

from talaria import __version__, relay, telegram
from talaria.telegram import NOT_NEWER
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
                                  "hub|w:hermes:status:x", "hub|x", "hub|up:latest", "hub|ofr:latest"])
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
    assert telegram.HELP == ("/status · /login [app] · /check [app|talaria] · /approve [app] <tag> · /reject [app] <tag> · "
                             "/rollback [app] [CONFIRM] · /backups [app] · "
                             "/restore [app] <id> [CONFIRM] · /update <version>")


def test_update_command_spawns_the_offer(bot):
    ctx, api, b = bot
    b.handle(upd(1, "/update v0.8.0"))
    assert api.sent() == ["Checking what Talaria v0.8.0 would change. I will send the result."]
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "update", "v0.8.0", "--offer"]]
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
    ex(b.hub, "clawvisor").on("hello", lines=[hello(protocol=2, app="clawvisor")])
    for text in ("/backups clawvisor", "/approve clawvisor v0.9.10", "/status"):
        b.handle(upd(1, text))
    assert api.sent() == [relay.VERSIONS, relay.VERSIONS,
                          "Hermes v1, running.\n\nClawvisor: " + relay.VERSIONS]
    b.handle(cb("clawvisor|ap:v0.9.10"))
    assert calls(api, "answerCallbackQuery")[0]["text"] == relay.VERSIONS
    b.handle(upd(2, "/update v0.8.0"))
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "update", "v0.8.0", "--offer"]]
    assert ["button", "ap:v0.9.10"] not in ex(b.hub, "clawvisor").ops()


def test_dispatch_error_is_replied(bot, monkeypatch):
    ctx, api, b = bot
    monkeypatch.setattr(telegram.relay, "status_texts", lambda *a, **k: 1 / 0)
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
    assert [c for c, _ in telegram.MENU] == ["status", "login", "check", "approve", "reject", "rollback",
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
    b.handle(cb("hub|up:v0.8.0"))
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "self-update", "v0.8.0"]]
    assert calls(api, "answerCallbackQuery")[0]["text"] == "Updating"
    assert calls(api, "editMessageReplyMarkup") == [status_markup("⬆️ Updating Talaria to v0.8.0…")]


def test_ofr_button_spawns_offer(bot):
    ctx, api, b = bot
    b.handle(cb("hub|ofr:v9.9.9"))
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "update", "v9.9.9", "--offer"]]
    assert calls(api, "answerCallbackQuery")[0]["text"] == "Checking"
    assert calls(api, "editMessageReplyMarkup") == [
        status_markup("🔎 Checking what Talaria v9.9.9 would change…")]


def test_ofr_button_refuses_not_newer(bot):
    ctx, api, b = bot
    b.handle(cb(f"hub|ofr:v{__version__}"))
    assert spawned(ctx) == []
    assert calls(api, "answerCallbackQuery")[0]["text"] == NOT_NEWER.format(
        tag=f"v{__version__}", cur=__version__)


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


def test_mismatch_heals_on_next_command(bot):
    ctx, api, b = bot
    b.mismatch = {"hermes"}
    assert b.app_command("hermes", "/status", []) == ("Hermes v1, running.", [])
    assert b.mismatch == set()


def test_mismatch_stays_while_hello_still_differs(bot):
    ctx, api, b = bot
    b.mismatch = {"hermes"}
    ex(b.hub, "hermes").on("hello", rc=2)
    assert b.app_command("hermes", "/status", []) == relay.VERSIONS
    assert b.mismatch == {"hermes"}


def test_mismatch_heals_on_button(bot):
    ctx, api, b = bot
    b.mismatch = {"hermes"}
    ex(b.hub, "hermes").on("button", lines=[line("button", toast="ok")])
    b.on_button("hermes|x")
    assert b.mismatch == set()


def test_update_same_version_allowed_when_lagging(bot):
    ctx, api, b = bot
    tag = f"v{__version__}"
    b.handle(upd(1, f"/update {tag}"))
    assert api.sent() == [NOT_NEWER.format(tag=tag, cur=__version__)]
    b.mismatch = {"hermes"}
    ex(b.hub, "hermes").on("hello", rc=2)
    b.handle(upd(2, f"/update {tag}"))
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "update", tag, "--offer"]]


@pytest.mark.parametrize("flag", ["mismatch", "skewed"])
@pytest.mark.parametrize("exc", [Unreachable("hermes"), NoAnswer("hermes")])
def test_unreachable_probe_keeps_the_lag_flag(bot, flag, exc):
    ctx, api, b = bot
    setattr(b, flag, {"hermes"})
    ex(b.hub, "hermes").on("hello", exc=exc)
    assert b._lagging() is True
    assert getattr(b, flag) == {"hermes"}


def test_update_button_same_version_allowed_when_lagging(bot):
    ctx, api, b = bot
    b.mismatch = {"hermes"}
    ex(b.hub, "hermes").on("hello", rc=2)
    b.handle(cb(f"hub|up:v{__version__}"))
    assert spawned(ctx) == [[str(ctx.paths.bin_link), "self-update", f"v{__version__}"]]


def test_version_skew_does_not_block_commands(bot):
    ctx, api, b = bot
    b.skewed = {"hermes"}
    ex(b.hub, "hermes").on("backups", lines=[reply("No backups.")])
    assert b.app_command("hermes", "/backups", [])[0] == "No backups."
    text, _ = b.app_command("hermes", "/status", [])
    assert text == f"Hermes v1, running.\n({relay.SKEW})"
    ex(b.hub, "hermes").on("button", lines=[line("button", toast="ok")])
    assert b.on_button("hermes|x")[0] != relay.VERSIONS


def test_startup_puts_a_version_skew_in_skewed_not_mismatch(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("hello", lines=[{**hello(), "version": "0.0.1"}]).on(
        "interrupted", lines=[reply("Interrupted deploy v1.")])
    b.startup()
    assert b.mismatch == set() and b.skewed == {"hermes"}
    assert ctx.notify.texts() == ["Interrupted deploy v1."]      # still reported
    b.handle(upd(1, f"/update v{__version__}"))
    assert spawned(ctx)       # the retry is offered


def test_update_same_version_not_allowed_once_the_app_caught_up(bot):
    ctx, api, b = bot
    b.skewed = {"hermes"}          # stale: hello now says equal
    b.handle(upd(1, f"/update v{__version__}"))
    assert api.sent() == [NOT_NEWER.format(tag=f"v{__version__}", cur=__version__)]
    assert b.skewed == set() and spawned(ctx) == []


RID = "e" * 64
LINKTEXT = "Open this link: https://h.example.ts.net/admin/auth/" + "f" * 64


def _login_bot(tmp_path, names=("hermes", "gbrain")):
    ctx, api, b = _bot(tmp_path, names)
    ex(b.hub, "gbrain").on("login-link", lines=[line("link", text=LINKTEXT)])
    return ctx, api, b


def test_login_picks_the_only_app_with_links(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, "/login"))
    sent = calls(api, "sendMessage")[-1]
    assert sent["text"] == LINKTEXT and sent["disable_web_page_preview"] is True
    assert ex(b.hub, "gbrain").ops()[-1] == ["login-link"]


def test_login_accepts_the_consent_url(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, f"/login gbrain https://h.example.ts.net/admin/?oauth_request={RID}&x=1#oauth-consent"))
    assert ex(b.hub, "gbrain").ops()[-1] == ["login-link", "--oauth-request", RID]


def test_login_accepts_the_bare_id(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, f"/login {RID}"))
    assert ex(b.hub, "gbrain").ops()[-1] == ["login-link", "--oauth-request", RID]


@pytest.mark.parametrize("arg", ["E" * 64, "e" * 63, "https://h/admin/?x=1", "a b"])
def test_login_bad_request_gets_usage(tmp_path, arg):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, f"/login gbrain {arg}"))
    assert api.sent()[-1] == telegram.LOGIN_USAGE
    assert ["login-link"] not in [o[:1] for o in ex(b.hub, "gbrain").ops()]


def test_login_hermes_lists_the_apps_with_links(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, "/login hermes"))
    assert api.sent()[-1] == "Login links: gbrain"


def test_login_unknown_app_and_no_login_apps(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, "/login nope"))
    assert api.sent()[-1] == "Unknown app: nope. Apps: hermes, gbrain"


def test_login_with_no_login_apps(bot):
    ctx, api, b = bot
    b.handle(upd(2, "/login"))
    assert api.sent()[-1] == "No app here offers login links."
    b.handle(upd(3, "/login hermes"))
    assert api.sent()[-1] == "No app here offers login links."


def test_login_asks_which_app_with_two(tmp_path):
    ctx, api, b = _login_bot(tmp_path, ("clawvisor", "gbrain"))
    b.handle(upd(2, "/login"))
    m = calls(api, "sendMessage")[-1]
    assert m["text"] == "Which app?"
    assert [k["callback_data"] for k in m["reply_markup"]["inline_keyboard"][0]] == \
        ["hub|ln:clawvisor", "hub|ln:gbrain"]


def test_login_gate_on_old_app_version(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    ex(b.hub, "gbrain").on("hello", lines=[hello(app="gbrain", version="0.6.1")])
    b.handle(upd(2, "/login gbrain"))
    assert api.sent()[-1] == telegram.UPDATE_FIRST.format(title="gbrain", ver="0.6.1", hub=__version__)
    assert ["login-link"] not in ex(b.hub, "gbrain").ops()


def test_login_refusal_is_replied(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    ex(b.hub, "gbrain").on("login-link", lines=[reply("gbrain is not running")])
    b.handle(upd(2, "/login"))
    assert api.sent()[-1] == "gbrain is not running"
    assert b.expiring == []


def test_expiry_replaces_the_link(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, "/login"))
    ctx.clock.sleep(299)
    b.expire_links()
    assert calls(api, "editMessageText") == []
    ctx.clock.sleep(2)
    b.expire_links()
    assert calls(api, "editMessageText") == [{
        "chat_id": OWNER, "message_id": 1, "disable_web_page_preview": True,
        "text": "Login link for gbrain expired. Send /login gbrain for a new one."}]
    assert b.expiring == []


def test_expiry_edit_failure_is_ignored_and_dropped(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, "/login"))
    real = api.call

    def failing(method, **p):
        if method == "editMessageText":
            raise ApiError(400, None)
        return real(method, **p)
    api.call = failing
    ctx.clock.sleep(301)
    b.expire_links()
    assert b.expiring == []


def test_poll_once_runs_the_expiry(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, "/login"))
    ctx.clock.sleep(301)
    b.poll_once()
    assert len(calls(api, "editMessageText")) == 1


def test_status_offers_login_buttons(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, "/status"))
    rows = calls(api, "sendMessage")[-1]["reply_markup"]["inline_keyboard"]
    assert rows == [[{"text": "🔑 Login link: gbrain", "callback_data": "hub|ln:gbrain"}]]


def test_app_status_adds_the_login_row(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, "/status gbrain"))
    rows = calls(api, "sendMessage")[-1]["reply_markup"]["inline_keyboard"]
    assert rows[-1] == [{"text": "🔑 Login link: gbrain", "callback_data": "hub|ln:gbrain"}]
    b.handle(upd(3, "/status hermes"))
    assert "reply_markup" not in calls(api, "sendMessage")[-1]


def test_login_button(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(cb("hub|ln:gbrain"))
    assert calls(api, "sendMessage")[-1]["text"] == LINKTEXT
    b.handle(cb("hub|ln:hermes"))
    assert api.sent()[-1] == "Login links: gbrain"


def test_login_with_two_apps_picks_the_one_that_takes_a_request(tmp_path):
    ctx, api, b = _login_bot(tmp_path, ("clawvisor", "gbrain"))
    b.handle(upd(2, f"/login {RID}"))
    assert ex(b.hub, "gbrain").ops()[-1] == ["login-link", "--oauth-request", RID]
    assert calls(api, "sendMessage")[-1]["text"] == LINKTEXT


def test_login_with_two_apps_and_a_junk_argument_gets_usage(tmp_path):
    ctx, api, b = _login_bot(tmp_path, ("clawvisor", "gbrain"))
    b.handle(upd(2, "/login 12"))
    assert api.sent()[-1] == telegram.LOGIN_USAGE


def test_login_never_writes_the_link_to_stdout_or_stderr(tmp_path, capsys):
    ctx, api, b = _login_bot(tmp_path)
    b.handle(upd(2, "/login"))
    assert calls(api, "sendMessage")[-1]["text"] == LINKTEXT
    o = capsys.readouterr()
    assert LINKTEXT not in o.out + o.err and "admin/auth" not in o.out + o.err


def test_send_link_api_error_leaves_no_link_in_reply_or_expiry(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    real = api.call

    def failing(method, **p):
        if method == "sendMessage" and p["text"] == LINKTEXT:
            raise ApiError(400, None)
        return real(method, **p)
    api.call = failing
    b.handle(upd(2, "/login"))
    assert b.expiring == []
    assert api.sent() and api.sent()[-1].startswith("Telegram refused the login link")
    assert all(LINKTEXT not in m["text"] and "admin/auth" not in m["text"] for m in calls(api, "sendMessage"))


def _gbrain_status(b, text):
    ex(b.hub, "gbrain").on("status", lines=[reply(text)])


def _rows(api):
    m = calls(api, "sendMessage")[-1]
    return [k["callback_data"] for r in m.get("reply_markup", {}).get("inline_keyboard", []) for k in r]


def test_status_login_row_only_for_running_apps(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    _gbrain_status(b, "gbrain v1 (abc), running.")
    b.handle(upd(2, "/status"))
    assert "hub|ln:gbrain" in _rows(api)
    _gbrain_status(b, "gbrain v1 (abc), not running.")
    b.handle(upd(3, "/status"))
    assert "hub|ln:gbrain" not in _rows(api)
    _gbrain_status(b, "gbrain says something odd")
    b.handle(upd(4, "/status"))
    assert "hub|ln:gbrain" not in _rows(api)


def test_app_status_login_row_only_when_running(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    _gbrain_status(b, "gbrain v1 (abc), running.")
    b.handle(upd(2, "/status gbrain"))
    assert "hub|ln:gbrain" in _rows(api)
    _gbrain_status(b, "gbrain v1 (abc), not running.")
    b.handle(upd(3, "/status gbrain"))
    assert "hub|ln:gbrain" not in _rows(api)


def test_status_reprobes_flagged_apps(tmp_path):
    ctx, api, b = _bot(tmp_path, ("hermes",))
    b.mismatch = {"hermes"}
    b.handle(upd(2, "/status"))
    assert api.sent()[-1] == "Hermes v1, running."
    assert b.mismatch == set()


def test_startup_leaves_an_app_ahead_out_of_both_sets_but_notes_it_in_status(bot):
    ctx, api, b = bot
    ex(b.hub, "hermes").on("hello", lines=[{**hello(), "version": "99.0.0"}])
    b.startup()
    assert b.mismatch == set() and b.skewed == set() and set(b.ahead) == {"hermes"}
    ahead = relay.AHEAD.format(ver="99.0.0")
    assert b.dispatch("/status", []) == f"Hermes v1, running.\n({ahead})"
    assert b.app_command("hermes", "/status", [])[0] == f"Hermes v1, running.\n({ahead})"
    b.handle(upd(1, f"/update v{__version__}"))
    assert api.sent()[-1] == NOT_NEWER.format(tag=f"v{__version__}", cur=__version__)
    assert spawned(ctx) == []


def test_status_drops_the_note_once_the_hub_caught_up_with_an_app_ahead(bot):
    ctx, api, b = bot
    b.ahead = {"hermes": relay.AHEAD.format(ver="99.0.0")}
    assert b.dispatch("/status", []) == "Hermes v1, running."
    assert b.ahead == {}


def test_status_skips_a_hanging_flagged_app(bot):
    ctx, api, b = bot
    b.skewed = {"hermes"}
    ex(b.hub, "hermes").on("hello", exc=NoAnswer("hermes"))
    assert b.dispatch("/status", []) == "Hermes did not answer in time"
    assert [c for c in ex(b.hub, "hermes").calls if c[1] == ["hello"]] == [
        ("call", ["hello"], telegram.PROBE_TIMEOUT)]
    assert telegram.PROBE_TIMEOUT == 10
    assert ["status"] not in ex(b.hub, "hermes").ops()


def test_status_still_asks_a_flagged_app_that_answers(bot):
    ctx, api, b = bot
    b.skewed = {"hermes"}
    ex(b.hub, "hermes").on("hello", lines=[{**hello(), "version": "0.0.1"}])
    assert b.dispatch("/status", []) == f"Hermes v1, running.\n({relay.SKEW})"


def _timeout_on_links(api):
    real = api.call

    def failing(method, **p):
        if method == "sendMessage" and p["text"] == LINKTEXT:
            raise ApiError(0, None)
        return real(method, **p)
    api.call = failing


def test_link_send_timeout_still_notifies_expiry(tmp_path, capsys):
    ctx, api, b = _login_bot(tmp_path)
    _timeout_on_links(api)
    b.handle(upd(2, "/login"))
    assert len(b.expiring) == 1 and b.expiring[0][1] is None
    o = capsys.readouterr()
    assert "login link for gbrain" in o.err and LINKTEXT not in o.out + o.err
    assert "admin/auth" not in o.out + o.err
    ctx.clock.sleep(301)
    b.expire_links()
    assert calls(api, "editMessageText") == []
    assert api.sent()[-1] == telegram.EXPIRED_UNSURE.format(title="gbrain", app="gbrain")
    assert b.expiring == []


def test_link_refused_by_telegram_is_replied_and_not_tracked(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    real = api.call

    def failing(method, **p):
        if method == "sendMessage" and p["text"] == LINKTEXT:
            raise ApiError(400, None, "Bad Request")
        return real(method, **p)
    api.call = failing
    b.handle(upd(2, "/login"))
    assert b.expiring == []
    t = api.sent()[-1]
    assert t.startswith("Telegram refused the login link for gbrain (") and \
        t.endswith("Send /login gbrain to try again.") and "admin/auth" not in t


def test_a_server_error_on_the_link_counts_as_maybe_delivered(tmp_path):
    ctx, api, b = _login_bot(tmp_path)
    real = api.call

    def failing(method, **p):
        if method == "sendMessage" and p["text"] == LINKTEXT:
            raise ApiError(502, None)
        return real(method, **p)
    api.call = failing
    b.handle(upd(2, "/login"))
    assert len(b.expiring) == 1 and b.expiring[0][1] is None
    assert not any(t.startswith("Telegram refused") for t in api.sent())

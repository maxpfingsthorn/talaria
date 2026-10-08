from __future__ import annotations

import re
import secrets
import sys
import time
from datetime import timedelta

from talaria import __version__, apps, hubexec, relay
from talaria.backup import ID_RE
from talaria.notify import ApiError, Message, TelegramAPI, keyboard
from talaria.op import STALE
from talaria.tags import SEMVER, TAG_ARG, semver_newer

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
NOT_NEWER = "Talaria {tag} is not newer than the installed v{cur}"
HELP = ("/status · /check [app|talaria] · /approve [app] <tag> · /reject [app] <tag> · "
        "/rollback [app] [CONFIRM] · /backups [app] · /restore [app] <id> [CONFIRM] · "
        "/update <version>")
MENU = [("status", "Every app: version, state, pending update"),
        ("check", "Look for new releases now: /check [app|talaria]"),
        ("approve", "Deploy a pending update: /approve [app] <tag>"),
        ("reject", "Never offer a release: /reject [app] <tag>"),
        ("rollback", "Undo the last change: /rollback [app] (asks to confirm)"),
        ("backups", "List backups: /backups [app]"),
        ("restore", "Restore a backup: /restore [app] <id> (asks to confirm)"),
        ("update", "Update Talaria itself: /update <version>")]
APP_WORD = re.compile(r"^[a-z][a-z_-]{0,31}$")
APP_CMDS = ("/status", "/backups", "/check", "/approve", "/reject", "/rollback", "/restore")
LONG_OPS = ("check", "deploy", "rollback", "restore")
OUT_OF_DATE = "⌛ Out of date"
FORMS = {"status": "/status", "backups": "/backups", "rollback": "/rollback"}


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


def _private_text(u: dict):
    msg = u.get("message") or {}
    if (msg.get("chat") or {}).get("type") != "private":
        return None, None
    return msg.get("from") or {}, (msg.get("text") or "").strip()


def pair(ctx, api, code: str, timeout_s: int = 900, announce=lambda: None) -> dict | None:
    """Drop the backlog, then announce the code, then wait for it."""
    backlog = api.call("getUpdates", offset=-1, timeout=0)
    offset = backlog[-1]["update_id"] + 1 if backlog else None
    if offset is not None:
        api.call("getUpdates", offset=offset, timeout=0)
    announce()
    deadline = ctx.now() + timedelta(seconds=timeout_s)
    while ctx.now() < deadline:
        for u in api.call("getUpdates", offset=offset, timeout=30) or []:
            offset = u["update_id"] + 1
            who, text = _private_text(u)
            if who and text == f"/pair {code}":
                api.call("getUpdates", offset=offset, timeout=0)
                api.call("sendMessage", chat_id=who["id"],
                         text="Paired. This chat now controls Talaria.")
                return who
    return None


def read_form(cmd: str, args: list[str]) -> str | None:
    """What a "Which app?" button runs for this command: its read or describe form, never
    a confirm (spec §5.1)."""
    if cmd in ("/status", "/backups") and not args:
        return cmd[1:]
    if cmd in ("/approve", "/reject") and len(args) == 1 and TAG_ARG.match(args[0]):
        return "status"
    if cmd == "/rollback" and args in ([], ["CONFIRM"]):
        return "rollback"
    if cmd == "/restore" and args and ID_RE.match(args[0]) and args[1:] in ([], ["CONFIRM"]):
        return f"restore:{args[0]}"
    return None


class Bot:
    def __init__(self, hub, api):
        self.hub, self.ctx, self.api = hub, hub.ctx, api
        self.offset = None
        self.mismatch: set[str] = set()

    def reply(self, text: str, buttons=None) -> None:
        params = {"chat_id": self.ctx.conf.telegram_user_id, "text": text[:4096]}
        markup = keyboard(buttons or [])
        if markup:
            params["reply_markup"] = markup
        self.api.call("sendMessage", **params)

    def _send(self, answer) -> None:
        text, buttons = answer if isinstance(answer, tuple) else (answer, None)
        if text:
            self.reply(text, buttons)

    def spawn(self, name: str, *argv: str) -> None:
        """Long work runs in a transient unit of this account, never in the bot process."""
        unit = f"talaria-{name}-{int(time.time())}"
        self.ctx.sh.run(["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}",
                         str(self.ctx.paths.bin_link), *argv])

    def dispatch(self, cmd: str, args: list[str]):
        nu = f"Not understood. Commands: {HELP}"
        if cmd == "/status" and not args:
            return relay.status_all(self.hub, self.mismatch)
        if cmd == "/update":
            if len(args) == 1 and SEMVER.match(args[0]):
                if not semver_newer(args[0], f"v{__version__}"):
                    return NOT_NEWER.format(tag=args[0], cur=__version__)
                self.spawn("update", "update", args[0], "--offer")
                return f"Checking what Talaria {args[0]} would change. I will send the result."
            return nu
        if cmd not in APP_CMDS:
            return nu
        names = list(self.hub.apps)
        if cmd == "/check" and not args:
            if not names:
                return "No apps registered."
            self.spawn("check", "check", "--report")
            titles = [e.title for e in self.hub.apps.values()]
            return f"Checking {', '.join(titles)} and Talaria."
        if cmd == "/check" and args == ["talaria"]:
            self.spawn("talaria-check", "check", "--talaria")
            return "Checking for a new Talaria release."
        if cmd == "/check" and args[:1] == ["talaria"]:
            return nu
        if args and args[0] in self.hub.apps:
            return self.app_command(args[0], cmd, args[1:])
        if args and APP_WORD.match(args[0]):
            return f"Unknown app: {args[0]}. Apps: {', '.join(names)}"
        if not names:
            return "No apps registered."
        if len(names) == 1:
            return self.app_command(names[0], cmd, args)
        form = read_form(cmd, args)
        if form is None:
            return nu
        return "Which app?", [[(e.title, f"hub|w:{n}:{form}") for n, e in self.hub.apps.items()]]

    def app_command(self, app: str, cmd: str, args: list[str]):
        if app in self.mismatch:
            return relay.VERSIONS
        e, a = self.hub.apps[app], apps.get(app)
        if cmd == "/status" and not args:
            return relay.quick(e, ["status"])
        if cmd == "/backups" and not args:
            return relay.quick(e, ["backups"])
        if cmd == "/check" and not args:
            self.spawn(f"{app}-check", "relay", app, "check")
            return f"Checking {e.title} for releases."
        if cmd in ("/approve", "/reject") and len(args) == 1 and a.is_release(args[0]):
            if cmd == "/reject":
                return relay.quick(e, ["reject", args[0]])
            self.spawn(f"{app}-deploy", "relay", app, "deploy", args[0])
            return f"Deploying {e.title} {args[0]}. I will report the result."
        if cmd == "/rollback" and not args:
            return relay.quick(e, ["rollback", "describe"])
        if cmd == "/rollback" and args == ["CONFIRM"]:
            self.spawn(f"{app}-rollback", "relay", app, "rollback", "confirm")
            return f"Rolling back {e.title}. I will report the result."
        if cmd == "/restore" and args and ID_RE.match(args[0]):
            if len(args) == 1:
                return relay.quick(e, ["restore", args[0], "describe"])
            if args[1:] == ["CONFIRM"]:
                self.spawn(f"{app}-restore", "relay", app, "restore", args[0], "confirm")
                return f"Restoring {e.title} {args[0]}. I will report the result."
        return f"Not understood. Commands: {HELP}"

    def hub_button(self, rest: str) -> tuple[str, str | None]:
        kind, _, arg = rest.partition(":")
        if kind == "up" and SEMVER.match(arg):
            if not semver_newer(arg, f"v{__version__}"):
                return NOT_NEWER.format(tag=arg, cur=__version__), None
            self.spawn("update", "self-update", arg)
            return "Updating", f"⬆️ Updating Talaria to {arg}…"
        if kind == "w":
            app, _, form = arg.partition(":")
            what, _, rid = form.partition(":")
            if app in self.hub.apps:
                if what == "restore" and ID_RE.match(rid):
                    self._send(self.app_command(app, "/restore", [rid]))
                    return self.hub.apps[app].title, None
                if what in FORMS and not rid:
                    self._send(self.app_command(app, FORMS[what], []))  # pragma: no mutate  (None and [] are both "no args")
                    return self.hub.apps[app].title, None
        return "Unknown button", None

    def on_button(self, data) -> tuple[str, str | None]:
        """(toast, status). Data is `<app>|<data>`; the app checks the tap against its own
        state (`op button`) and names what to run. `hub|…` buttons are the hub's own."""
        data = data or ""  # pragma: no mutate  (any other default is also an unknown button)
        if data == "done":
            return "Already handled", None
        app, sep, rest = data.partition("|")
        if sep and app == "hub":
            return self.hub_button(rest)
        if not sep or app not in self.hub.apps:
            return STALE, OUT_OF_DATE
        if app in self.mismatch:
            return relay.VERSIONS, None
        e = self.hub.apps[app]
        try:
            lines = e.executor.call(["button", rest])
        except hubexec.Unreachable:
            return relay.unreachable_text(e.title), None
        except hubexec.NoAnswer:
            return relay.no_answer_text(e.title), None
        b = next((d for d in lines if d.get("kind") == "button"), None)
        if b is None:
            return "Unknown button", None
        run = b.get("run")
        if isinstance(run, list) and run and run[0] in LONG_OPS \
                and all(isinstance(x, str) for x in run):
            self.spawn(f"{app}-{run[0]}", "relay", app, *run)
        return str(b.get("toast") or ""), b.get("status")

    def handle_button(self, q: dict) -> None:
        msg = q.get("message") or {}
        chat = msg.get("chat") or {}
        if (q.get("from") or {}).get("id") != self.ctx.conf.telegram_user_id \
                or chat.get("type") != "private":
            print(f"[talaria] ignored button {q.get('id')}", file=sys.stderr)
            return
        try:
            toast, status = self.on_button(q.get("data"))
        except Exception as e:
            toast, status = f"Error: {e}"[:200], None  # pragma: no mutate  (the answer below cuts at 200 again)
        # quiet: a late tap gets 400 "query is too old"; that must not stop the rest
        self._try("answerCallbackQuery", callback_query_id=q.get("id"), text=toast[:200])
        if status is None:
            return
        self._try("editMessageReplyMarkup", chat_id=chat.get("id"),
                  message_id=msg.get("message_id"),
                  reply_markup={"inline_keyboard": [[{"text": status, "callback_data": "done"}]]})

    def _try(self, method: str, **params) -> None:
        try:
            self.api.call(method, **params)
        except ApiError as e:
            print(f"[talaria] telegram {method}: {e}", file=sys.stderr)

    def handle(self, u: dict) -> None:
        if "callback_query" in u:
            self.handle_button(u["callback_query"])
            return
        who, text = _private_text(u)
        if not who or who.get("id") != self.ctx.conf.telegram_user_id or not text:
            print(f"[talaria] ignored update {u.get('update_id')}", file=sys.stderr)
            return
        parts = text.split()
        cmd = parts[0].split("@", 1)[0]  # pragma: no mutate  (maxsplit does not change element 0; rsplit is pinned by a test)
        try:
            answer = self.dispatch(cmd, parts[1:])
        except Exception as e:
            answer = f"Error: {e}"
        self._send(answer)

    def startup(self) -> None:
        try:   # the "Menu" button in the chat; shown to the owner only
            self.api.call("setMyCommands",
                          commands=[{"command": c, "description": d} for c, d in MENU],
                          scope={"type": "chat", "chat_id": self.ctx.conf.telegram_user_id})
        except ApiError as e:
            print(f"[talaria] could not set the command menu: {e}", file=sys.stderr)
        backlog = self.api.call("getUpdates", offset=-1, timeout=0)
        self.offset = backlog[-1]["update_id"] + 1 if backlog else None
        if self.offset is not None:
            self.api.call("getUpdates", offset=self.offset, timeout=0)
        # spec §4.4: each app's protocol, once per bot start (an update restarts the bot)
        self.mismatch = {n for n, e in self.hub.apps.items() if (relay.hello(e) or "").startswith(relay.VERSIONS)}
        for n, e in self.hub.apps.items():
            if n not in self.mismatch:
                text, _ = relay.quick(e, ["interrupted"])
                if text:
                    self.ctx.notify.send(Message(text))

    def poll_once(self) -> None:
        for u in self.api.call("getUpdates", offset=self.offset, timeout=25,
                               allowed_updates=["message", "callback_query"]) or []:
            self.offset = u["update_id"] + 1
            self.handle(u)


def run(hub) -> int:
    conf = hub.ctx.conf
    if not conf.telegram_token or not conf.telegram_user_id:
        print("talaria bot: token or user id missing; run talaria setup", file=sys.stderr)
        return 1
    bot = Bot(hub, TelegramAPI(conf.telegram_api, conf.telegram_token))
    delay = 0      # the first failure retries at once: usually one dropped connection
    while True:
        try:
            if bot.offset is None:
                bot.startup()
                bot.offset = bot.offset or 0
            bot.poll_once()
            delay = 0
        except ApiError as e:
            print(f"[talaria] telegram: {e}", file=sys.stderr)
            if delay:
                time.sleep(delay)
            delay = min(max(delay * 2, 1), 60)

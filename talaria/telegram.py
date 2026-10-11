from __future__ import annotations

import re
import secrets
import sys
import time
import urllib.parse
from datetime import timedelta

from talaria import __version__, apps, hubexec, relay
from talaria.apps.base import OAUTH_REQUEST_RE
from talaria.backup import ID_RE
from talaria.notify import ApiError, Message, TelegramAPI, keyboard
from talaria.op import STALE
from talaria.tags import SEMVER, TAG_ARG, semver_newer

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
NOT_NEWER = "Talaria {tag} is not newer than the installed v{cur}"
HELP = ("/status · /login [app] · /check [app|talaria] · /approve [app] <tag> · /reject [app] <tag> · "
        "/rollback [app] [CONFIRM] · /backups [app] · /restore [app] <id> [CONFIRM] · "
        "/update <version>")
MENU = [("status", "Every app: version, state, pending update"),
        ("login", "Get a dashboard login link: /login [app]"),
        ("check", "Look for new releases now: /check [app|talaria]"),
        ("approve", "Deploy a pending update: /approve [app] <tag>"),
        ("reject", "Never offer a release: /reject [app] <tag>"),
        ("rollback", "Undo the last change: /rollback [app] (asks to confirm)"),
        ("backups", "List backups: /backups [app]"),
        ("restore", "Restore a backup: /restore [app] <id> (asks to confirm)"),
        ("update", "Update Talaria itself: /update <version>")]
FIRST_LOGIN_RELEASE = "v0.7.0"
LINK_TTL = 300   # seconds until the expiry notice replaces the link
UPDATE_FIRST = "{title} runs Talaria {ver}; update Talaria first: /update v{hub}"
EXPIRED = "Login link for {title} expired. Send /login {app} for a new one."
LOGIN_USAGE = "Usage: /login [app] [OAuth request id or consent URL]"
NO_LOGIN_APP = "No app here offers login links."
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
        self.mismatch: set[str] = set()   # protocol differs or hello fails: commands are blocked
        self.skewed: set[str] = set()     # only the version differs: a note, /update retries it
        self.expiring: list[tuple] = []   # (due, message_id, title, app): links to replace

    def _login_row(self, app: str) -> list:
        return [(f"🔑 Login link: {self.hub.apps[app].title}", f"hub|ln:{app}")]

    def login(self, args: list[str]):
        supported = [n for n in self.hub.apps if apps.get(n).has_login_link]
        none = f"Login links: {', '.join(self.hub.apps[n].title for n in supported)}" \
            if supported else NO_LOGIN_APP
        if args and args[0] in self.hub.apps:
            app, rest = args[0], args[1:]
        elif args and APP_WORD.match(args[0]):
            return f"Unknown app: {args[0]}. Apps: {', '.join(self.hub.apps)}"
        elif len(supported) == 1:
            app, rest = supported[0], args
        elif not supported:
            return NO_LOGIN_APP
        elif args:
            takers = [n for n in supported if apps.get(n).takes_oauth_request]
            if len(takers) != 1:
                return LOGIN_USAGE
            app, rest = takers[0], args
        else:
            return "Which app?", [[(self.hub.apps[n].title, f"hub|ln:{n}") for n in supported]]
        if app not in supported:
            return none
        request = None
        if len(rest) > 1:
            return LOGIN_USAGE
        if rest:
            x = rest[0]
            request = urllib.parse.parse_qs(urllib.parse.urlsplit(x).query).get(
                "oauth_request", [""])[0] if x.startswith("http") else x
            if not OAUTH_REQUEST_RE.match(request):
                return LOGIN_USAGE
        if self._still_mismatched(app):
            return relay.VERSIONS
        e = self.hub.apps[app]
        v = relay.version(e)
        if v and semver_newer(FIRST_LOGIN_RELEASE, f"v{v}"):
            return UPDATE_FIRST.format(title=e.title, ver=v, hub=__version__)
        text, is_link = relay.login(e, request)
        if not is_link:
            return text
        self.send_link(app, text)
        return None

    def send_link(self, app: str, text: str) -> None:
        r = self.api.call("sendMessage", chat_id=self.ctx.conf.telegram_user_id,
                          text=text[:4096], disable_web_page_preview=True)
        self.expiring.append((self.ctx.now() + timedelta(seconds=LINK_TTL), r["message_id"],
                              self.hub.apps[app].title, app))

    def expire_links(self) -> None:
        now = self.ctx.now()
        due = [x for x in self.expiring if x[0] <= now]
        self.expiring = [x for x in self.expiring if x[0] > now]
        for _, mid, title, app in due:
            self._try("editMessageText", chat_id=self.ctx.conf.telegram_user_id, message_id=mid,
                      text=EXPIRED.format(title=title, app=app), disable_web_page_preview=True)

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

    def _update_allowed(self, tag: str) -> str | None:
        """None if `tag` may be installed: newer, or the hub's own version while an app lags."""
        if semver_newer(tag, f"v{__version__}"):
            return None
        if tag == f"v{__version__}" and self._lagging():
            return None
        return NOT_NEWER.format(tag=tag, cur=__version__)

    def _lagging(self) -> bool:
        """Probe the flagged apps again: one may have caught up since the bot started."""
        for app in sorted(self.mismatch | self.skewed):
            err = relay.hello(self.hub.apps[app]) or ""
            self.mismatch.discard(app)
            self.skewed.discard(app)
            if err.startswith(relay.VERSIONS):
                self.mismatch.add(app)
            elif err.startswith(relay.SKEW):
                self.skewed.add(app)
        return bool(self.mismatch or self.skewed)

    def _still_mismatched(self, app: str) -> bool:
        """An app flagged at startup is probed again: it may have been updated since."""
        if app not in self.mismatch:
            return False
        err = relay.hello(self.hub.apps[app])
        if err is None or err.startswith(relay.SKEW):
            self.mismatch.discard(app)
            if err:
                self.skewed.add(app)
            return False
        return True

    def dispatch(self, cmd: str, args: list[str]):
        nu = f"Not understood. Commands: {HELP}"
        if cmd == "/status" and not args:
            self._lagging()
            texts = relay.status_texts(self.hub, self.mismatch, self.skewed)
            text = "\n\n".join(b for b in texts.values() if b) or "No apps registered."
            rows = [self._login_row(n) for n, t in texts.items()
                    if apps.get(n).has_login_link and n not in self.mismatch and relay.is_running(t)]
            return (text, rows) if rows else text
        if cmd == "/login":
            return self.login(args)
        if cmd == "/update":
            if len(args) == 1 and SEMVER.match(args[0]):
                if (no := self._update_allowed(args[0])):
                    return no
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
        if self._still_mismatched(app):
            return relay.VERSIONS
        e, a = self.hub.apps[app], apps.get(app)
        if cmd == "/status" and not args:
            text, buttons = relay.quick(e, ["status"])
            if a.has_login_link and relay.is_running(text):
                buttons = [*buttons, self._login_row(app)]
            return (relay.with_skew(text) if app in self.skewed else text), buttons
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
            if (no := self._update_allowed(arg)):
                return no, None
            self.spawn("update", "self-update", arg)
            return "Updating", f"⬆️ Updating Talaria to {arg}…"
        if kind == "ofr" and SEMVER.match(arg):
            if (no := self._update_allowed(arg)):
                return no, None
            self.spawn("update", "update", arg, "--offer")
            return "Checking", f"🔎 Checking what Talaria {arg} would change…"
        if kind == "ln" and arg in self.hub.apps:
            self._send(self.login([arg]))
            return self.hub.apps[arg].title, None
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
        if self._still_mismatched(app):
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
        probes = {n: relay.hello(e) or "" for n, e in self.hub.apps.items()}
        self.mismatch = {n for n, h in probes.items() if h.startswith(relay.VERSIONS)}
        self.skewed = {n for n, h in probes.items() if h.startswith(relay.SKEW)}
        for n, e in self.hub.apps.items():
            if n not in self.mismatch:
                text, _ = relay.quick(e, ["interrupted"])
                if text:
                    self.ctx.notify.send(Message(text))

    def poll_once(self) -> None:
        self.expire_links()
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

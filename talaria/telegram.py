from __future__ import annotations

import secrets
import sys
import time
from datetime import timedelta

from talaria import rollback, status
from talaria.backup import ID_RE
from talaria.notify import ApiError, Message, TelegramAPI, keyboard
from talaria.tags import RELEASE_TAG

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
HELP = ("/status · /check · /approve <tag> · /reject <tag> · /rollback [CONFIRM] · "
        "/backups · /restore <id> [CONFIRM]")


MENU = [("status", "Hermes version, state, pending update"),
        ("check", "Look for a new Hermes release now"),
        ("approve", "Deploy the pending update: /approve <tag>"),
        ("reject", "Never offer a release: /reject <tag>"),
        ("rollback", "Undo the last change (asks to confirm)"),
        ("backups", "List backups"),
        ("restore", "Restore a backup: /restore <id> (asks to confirm)")]
STALE = "Out of date — send /status"


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


class Bot:
    def __init__(self, ctx, api):
        self.ctx, self.api = ctx, api
        self.offset = None

    def reply(self, text: str, buttons=None) -> None:
        params = {"chat_id": self.ctx.conf.telegram_user_id, "text": text[:4096]}
        markup = keyboard(buttons or [])
        if markup:
            params["reply_markup"] = markup
        self.api.call("sendMessage", **params)

    def spawn(self, *argv: str) -> None:
        unit = f"talaria-op-{argv[0]}-{int(time.time())}"
        self.ctx.sh.run(["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}",
                         str(self.ctx.paths.bin_link), *argv])

    def dispatch(self, cmd: str, args: list[str]) -> str | None:
        ctx = self.ctx
        if cmd == "/status" and not args:
            return status.status_text(ctx)
        if cmd == "/backups" and not args:
            return status.backups_text(ctx)
        if cmd == "/check" and not args:
            self.spawn("check")
            return "Checking for releases."
        if cmd == "/approve" and len(args) == 1 and RELEASE_TAG.match(args[0]):
            self.spawn("deploy", args[0])
            return f"Deploying {args[0]}. I will report the result."
        if cmd == "/reject" and len(args) == 1 and RELEASE_TAG.match(args[0]):
            from talaria.cli import reject
            return reject(ctx, args[0])
        if cmd == "/rollback" and not args:
            return rollback.describe(ctx), rollback.describe_buttons(ctx)
        if cmd == "/rollback" and args == ["CONFIRM"]:
            self.spawn("rollback", "--confirm")
            return "Rolling back. I will report the result."
        if cmd == "/restore" and len(args) == 1 and ID_RE.match(args[0]):
            return (rollback.describe_restore(ctx, args[0]),
                    rollback.describe_restore_buttons(ctx, args[0]))
        if cmd == "/restore" and len(args) == 2 and ID_RE.match(args[0]) and args[1] == "CONFIRM":
            self.spawn("restore", args[0], "--confirm")
            return f"Restoring {args[0]}. I will report the result."
        return f"Not understood. Commands: {HELP}"

    def on_button(self, data) -> tuple[str, str | None]:
        """(toast, status). The status label replaces the buttons in place; None keeps them.
        Each button names what it acts on and is checked against the current state, so an
        old button never acts on a different target."""
        from talaria import state
        kind, _, arg = (data or "").partition(":")
        if kind == "done":
            return "Already handled", None
        st = state.load(self.ctx.paths)
        if kind in ("ap", "rj") and RELEASE_TAG.match(arg):
            if kind == "rj":
                from talaria.cli import reject
                if reject(self.ctx, arg).startswith("Busy"):
                    return "Busy, try again in a minute", None
                return "Rejected", f"❌ Rejected {arg}"
            if (st.get("pending") or {}).get("tag") != arg:
                return STALE, "⌛ Out of date"
            self.spawn("deploy", arg)
            return "Deploying", f"✅ Approved — deploying {arg}"
        if kind == "rb" and (arg == "resume" or ID_RE.match(arg)):
            resume = rollback.needs_resume(self.ctx, st)
            t = None if resume else rollback.target(self.ctx, st)
            if (arg == "resume" and resume) or (t and t[0] == arg):
                self.spawn("rollback", "--confirm")
                return "Rolling back", "↩️ Rolling back…"
            return STALE, "⌛ Out of date"
        if kind == "rs" and ID_RE.match(arg):
            if not rollback.describe_restore_buttons(self.ctx, arg):
                return STALE, "⌛ Out of date"
            self.spawn("restore", arg, "--confirm")
            return "Restoring", f"↩️ Restoring {arg}…"
        return "Unknown button", None

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
            toast, status = f"Error: {e}"[:200], None
        # quiet: a late tap gets 400 "query is too old"; that must not stop the rest
        self._try("answerCallbackQuery", callback_query_id=q.get("id"), text=toast)
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
        cmd = parts[0].split("@", 1)[0]
        try:
            answer = self.dispatch(cmd, parts[1:])
        except Exception as e:
            answer = f"Error: {e}"
        if isinstance(answer, tuple):
            self.reply(*answer)
        elif answer:
            self.reply(answer)

    def startup(self) -> None:
        try:   # the "Menu" button in the chat; shown to the owner only
            self.api.call("setMyCommands", commands=[{"command": c, "description": d}
                                                     for c, d in MENU],
                          scope={"type": "chat", "chat_id": self.ctx.conf.telegram_user_id})
        except ApiError as e:
            print(f"[talaria] could not set the command menu: {e}", file=sys.stderr)
        backlog = self.api.call("getUpdates", offset=-1, timeout=0)
        self.offset = backlog[-1]["update_id"] + 1 if backlog else None
        if self.offset is not None:
            self.api.call("getUpdates", offset=self.offset, timeout=0)
        from talaria import state
        it = status.interrupted_text(self.ctx, state.load(self.ctx.paths))
        if it:
            self.ctx.notify.send(Message(it))

    def poll_once(self) -> None:
        for u in self.api.call("getUpdates", offset=self.offset, timeout=30,
                               allowed_updates=["message", "callback_query"]) or []:
            self.offset = u["update_id"] + 1
            self.handle(u)


def run(ctx) -> int:
    if not ctx.conf.telegram_token or not ctx.conf.telegram_user_id:
        print("talaria bot: token or user id missing; run talaria setup", file=sys.stderr)
        return 1
    bot = Bot(ctx, TelegramAPI(ctx.conf.telegram_api, ctx.conf.telegram_token))
    delay = 1
    while True:
        try:
            if bot.offset is None:
                bot.startup()
                bot.offset = bot.offset or 0
            bot.poll_once()
            delay = 1
        except ApiError as e:
            print(f"[talaria] telegram: {e}", file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, 60)

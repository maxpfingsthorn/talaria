from __future__ import annotations

import secrets
import sys
import time
from datetime import timedelta

from talaria import rollback, status
from talaria.backup import ID_RE
from talaria.notify import ApiError, Message, TelegramAPI
from talaria.tags import RELEASE_TAG

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
HELP = ("/status · /check · /approve <tag> · /reject <tag> · /rollback [CONFIRM] · "
        "/backups · /restore <id> [CONFIRM]")


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

    def reply(self, text: str) -> None:
        self.api.call("sendMessage", chat_id=self.ctx.conf.telegram_user_id, text=text[:4096])

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
            return rollback.describe(ctx)
        if cmd == "/rollback" and args == ["CONFIRM"]:
            self.spawn("rollback", "--confirm")
            return "Rolling back. I will report the result."
        if cmd == "/restore" and len(args) == 1 and ID_RE.match(args[0]):
            return rollback.describe_restore(ctx, args[0])
        if cmd == "/restore" and len(args) == 2 and ID_RE.match(args[0]) and args[1] == "CONFIRM":
            self.spawn("restore", args[0], "--confirm")
            return f"Restoring {args[0]}. I will report the result."
        return f"Not understood. Commands: {HELP}"

    def handle(self, u: dict) -> None:
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
        if answer:
            self.reply(answer)

    def startup(self) -> None:
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
                               allowed_updates=["message"]) or []:
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

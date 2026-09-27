from __future__ import annotations

import html
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

LIMIT = 4096
CUT = "\n…(truncated, full text in the journal)"


@dataclass
class Message:
    text: str
    untrusted: list = field(default_factory=list)
    commands: list = field(default_factory=list)
    buttons: list = field(default_factory=list)    # rows of (label, callback_data)


def keyboard(rows) -> dict | None:
    """Telegram inline keyboard; callback_data is limited to 64 bytes, so longer ones
    are dropped (the typed command in the text still works)."""
    out = [[{"text": label, "callback_data": data} for label, data in row
            if len(data.encode()) <= 64] for row in rows]
    out = [r for r in out if r]
    return {"inline_keyboard": out} if out else None


def _assemble(text: str, blocks: list[str], commands: list[str]) -> str:
    parts = [html.escape(text)]
    parts += [f"<pre>{html.escape(b)}</pre>" for b in blocks]
    if commands:
        parts.append(" · ".join(f"<code>{html.escape(c)}</code>" for c in commands))
    return "\n\n".join(parts)


def render(m: Message, limit: int = LIMIT) -> str:
    out = _assemble(m.text, m.untrusted, m.commands)
    if len(out) <= limit or not m.untrusted:
        return out[:limit]
    fixed = len(_assemble(m.text, ["" for _ in m.untrusted], m.commands))
    budget = max(0, (limit - fixed) // len(m.untrusted))
    blocks = []
    for b in m.untrusted:
        # escaping can grow text up to 6x (&quot;); shrink until it fits
        n = min(len(b), budget)
        while n > 0 and len(html.escape(b[:n] + CUT)) > budget:
            n = int(n * 0.8)
        blocks.append(b if len(html.escape(b)) <= budget else b[:n] + CUT)
    return _assemble(m.text, blocks, m.commands)[:limit]


class ApiError(Exception):
    def __init__(self, status: int, retry_after: int | None):
        super().__init__(f"telegram api status {status}")
        self.status, self.retry_after = status, retry_after


class TelegramAPI:
    def __init__(self, base: str, token: str):
        self.base, self.token = base.rstrip("/"), token

    def call(self, method: str, **params):
        # `timeout` in params is Telegram's long-poll timeout; the HTTP timeout must exceed it
        http_timeout = float(params.get("timeout") or 0) + 15
        req = urllib.request.Request(
            f"{self.base}/bot{self.token}/{method}",
            data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as r:
                body = json.loads(r.read())
        except urllib.error.HTTPError as e:
            retry = None
            try:
                retry = json.loads(e.read()).get("parameters", {}).get("retry_after")
            except Exception:
                pass
            raise ApiError(e.code, retry) from None
        except (urllib.error.URLError, OSError, ValueError):
            raise ApiError(0, None) from None
        if not body.get("ok"):
            raise ApiError(400, None)
        return body["result"]


class TelegramNotifier:
    def __init__(self, conf, api=None, sleep=time.sleep):
        self.conf, self.sleep = conf, sleep
        self.api = api or TelegramAPI(conf.telegram_api, conf.telegram_token)

    def send(self, m: Message) -> None:
        print(f"[talaria] message: {m.text}", *m.untrusted, file=sys.stderr, sep="\n")
        if not self.conf.telegram_token or not self.conf.telegram_user_id:
            return
        text = render(m)
        for attempt in range(3):
            try:
                params = {"chat_id": self.conf.telegram_user_id, "text": text,
                          "parse_mode": "HTML", "disable_web_page_preview": True}
                markup = keyboard(m.buttons)
                if markup:
                    params["reply_markup"] = markup
                self.api.call("sendMessage", **params)
                return
            except ApiError as e:
                if e.status == 429 and e.retry_after:
                    self.sleep(e.retry_after)
                elif 400 <= e.status < 500 and e.status != 429:
                    break
                else:
                    self.sleep(2 ** attempt)
        print("[talaria] telegram delivery failed; message above is in the journal only",
              file=sys.stderr)

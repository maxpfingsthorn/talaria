"""Minimal Telegram Bot API stand-in for e2e tests. Run in a thread."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class State:
    def __init__(self):
        self.lock = threading.Condition()
        self.updates, self.sent, self.next_id = [], [], 1
        self.cursor = 0  # wait_sent only looks at messages after the last match
        self.buttons = []  # reply_markup per sent message, parallel to sent

    def inject(self, text, user=4242, chat_type="private"):
        with self.lock:
            self.updates.append({"update_id": self.next_id, "message": {
                "text": text, "chat": {"id": user, "type": chat_type},
                "from": {"id": user, "first_name": "Test", "username": "tester"}}})
            self.next_id += 1
            self.lock.notify_all()

    def tap(self, data, user=4242):
        """Simulate tapping an inline button."""
        with self.lock:
            self.updates.append({"update_id": self.next_id, "callback_query": {
                "id": f"q{self.next_id}", "data": data, "from": {"id": user},
                "message": {"message_id": 1, "chat": {"id": user, "type": "private"}}}})
            self.next_id += 1
            self.lock.notify_all()

    def wait_sent(self, needle, timeout=240):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                for i in range(self.cursor, len(self.sent)):
                    if needle in self.sent[i]:
                        self.cursor = i + 1
                        return self.sent[i]
            time.sleep(0.5)
        raise AssertionError(f"no message containing {needle!r}; sent: {self.sent[-5:]}")


def make_server(port=8081):
    st = State()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            method = self.path.rsplit("/", 1)[-1]
            n = int(self.headers.get("Content-Length") or 0)
            params = json.loads(self.rfile.read(n) or b"{}")
            if method == "getMe":
                result = {"id": 1, "username": "fakebot"}
            elif method == "sendMessage":
                with st.lock:
                    st.sent.append(params["text"])
                    st.buttons.append(params.get("reply_markup"))
                result = {"message_id": len(st.sent)}
            elif method == "getUpdates":
                result = self.get_updates(params)
            else:
                result = True
            body = json.dumps({"ok": True, "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def get_updates(self, p):
            off, timeout = p.get("offset"), min(p.get("timeout", 0), 2)
            with st.lock:
                if off == -1:
                    return st.updates[-1:]
                if off is not None:
                    st.updates = [u for u in st.updates if u["update_id"] >= off]
                if not st.updates and timeout:
                    st.lock.wait(timeout)
                return list(st.updates)

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, st

import threading
from datetime import timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from talaria import apps
from talaria import ctx as ctxmod
from talaria.notify import TelegramNotifier
from talaria.shell import Shell


def serve(code, body):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(code)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/x"


def test_http_get_ok():
    srv, url = serve(200, b"hello")
    try:
        assert ctxmod.http_get(url) == (200, b"hello")
    finally:
        srv.shutdown()


def test_http_get_error_status():
    srv, url = serve(503, b"busy")
    try:
        assert ctxmod.http_get(url, 2.0) == (503, b"")
    finally:
        srv.shutdown()


def test_http_get_unreachable():
    srv, url = serve(200, b"")
    srv.shutdown()
    srv.server_close()
    assert ctxmod.http_get(url, 1.0) == (0, b"")


def test_utcnow_is_aware():
    assert ctxmod._utcnow().tzinfo is timezone.utc


def test_make_ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".config/talaria").mkdir(parents=True)
    (tmp_path / ".config/talaria/talaria.conf").write_text("backup.keep = 2\n")
    c = ctxmod.make_ctx()
    assert c.paths.home == tmp_path and c.conf.backup_keep == 2
    assert isinstance(c.sh, Shell) and isinstance(c.notify, TelegramNotifier)
    assert c.notify.conf is c.conf
    assert c.http_get is ctxmod.http_get and c.now is ctxmod._utcnow


def test_make_ctx_sets_app_and_paths_app_from_conf(tmp_path, monkeypatch):
    # Only "hermes" is a real app in Part A, so "hermes" alone can't tell correct
    # wiring apart from a hard-coded default. A fake app name (validated and resolved
    # through a monkeypatched apps.get, exactly as conf.py's own validation calls it)
    # gives a value that isn't the default, so this kills ctx.x_make_ctx__mutmut_6
    # (Paths(home, None)), _8 (Paths(home) with no app), _13 (app=None) and _18 (the
    # app= kwarg dropped entirely).
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    sentinel = object()
    real_get = apps.get
    monkeypatch.setattr(apps, "get", lambda name: sentinel if name == "other" else real_get(name))
    (tmp_path / ".config/talaria").mkdir(parents=True)
    # every key conf.py would otherwise fill in from the app's defaults is pinned here too,
    # so load_conf never has to read an attribute off the sentinel "app" object below.
    (tmp_path / ".config/talaria/talaria.conf").write_text(
        "app = other\ndata_dir = ~/x\ndashboard.port = 1\nrepo = x\nimage = x\n"
        "min_release = x\nbackup.exclude = x\n")
    c = ctxmod.make_ctx()
    assert c.paths.app == "other"
    assert c.app is sentinel


def test_paths_layout(tmp_path):
    p = ctxmod.Paths(tmp_path)
    assert {k: str(getattr(p, k)).replace(str(tmp_path), "~") for k in (
        "conf_dir", "conf_file", "env_file", "hermes_env", "state_dir", "state_file", "lock_file",
        "marker", "backups", "staging", "history", "install_dir", "bin_link", "quadlet_dir",
        "quadlet", "units_dir")} == {
        "conf_dir": "~/.config/talaria", "conf_file": "~/.config/talaria/talaria.conf",
        "env_file": "~/.config/talaria/.env", "hermes_env": "~/.config/talaria/hermes.env",
        "state_dir": "~/.local/state/talaria", "state_file": "~/.local/state/talaria/state.json",
        "lock_file": "~/.local/state/talaria/lock", "marker": "~/.local/state/talaria/changing",
        "backups": "~/.local/state/talaria/backups", "staging": "~/.local/state/talaria/staging",
        "history": "~/.local/state/talaria/history", "install_dir": "~/.local/share/talaria",
        "bin_link": "~/.local/bin/talaria", "quadlet_dir": "~/.config/containers/systemd",
        "quadlet": "~/.config/containers/systemd/hermes.container",
        "units_dir": "~/.config/systemd/user"}
    assert (p.helpers_dir / "migrate.py").exists() and (p.templates_dir / "hermes.container").exists()

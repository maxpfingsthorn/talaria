import pytest
import json
import sqlite3

from helpers import confdiff, dbopen, migrate


class FakeUp:
    def __init__(self, before=27, latest=30, after=30, rc=0, raise_exc=None, print_msg="✓ step"):
        self.v = before
        self.latest, self.after, self.rc = latest, after, rc
        self.raise_exc, self.print_msg = raise_exc, print_msg

    def config_versions(self):
        return self.v, self.latest

    def run_config_migration(self):
        print(self.print_msg)
        if self.raise_exc:
            raise self.raise_exc
        self.v = self.after
        return self.rc


def test_migrate_success_captures_messages():
    r = migrate.migrate(FakeUp())
    assert r["ok"] and (r["before"], r["after"], r["latest"]) == (27, 30, 30)
    assert r["messages"] == ["✓ step"] and r["error"] is None


def test_migrate_not_advanced_is_failure():
    r = migrate.migrate(FakeUp(after=27))
    assert not r["ok"] and "expected 30" in r["error"]


def test_migrate_nonzero_exit_is_failure():
    assert not migrate.migrate(FakeUp(rc=1))["ok"]


def test_migrate_exception_and_systemexit_are_failures():
    assert "boom" in migrate.migrate(FakeUp(raise_exc=RuntimeError("boom")))["error"]
    assert not migrate.migrate(FakeUp(raise_exc=SystemExit(1)))["ok"]


def test_migrate_already_current_is_ok():
    r = migrate.migrate(FakeUp(before=30, after=30))
    assert r["ok"] and r["before"] == r["after"] == 30


def make_db(path, version):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE schema_version (version INTEGER)")
    c.execute("INSERT INTO schema_version VALUES (?)", (version,))
    c.commit()
    c.close()


class DbUp:
    def __init__(self, to=30, fail=False):
        self.to, self.fail = to, fail

    def open_state_db(self, path):
        if self.fail:
            raise RuntimeError("corrupt")
        c = sqlite3.connect(path)
        c.execute("UPDATE schema_version SET version = ?", (self.to,))
        c.commit()
        c.close()

    def schema_version(self):
        return 30


def test_dbopen_reports_versions(tmp_path):
    db = tmp_path / "state.db"
    make_db(db, 25)
    assert dbopen.dbopen(DbUp(), db) == {"ok": True, "before": 25, "after": 30,
                                         "schema_version": 30, "error": None}


def test_dbopen_held_back_is_ok(tmp_path):
    db = tmp_path / "state.db"
    make_db(db, 25)
    r = dbopen.dbopen(DbUp(to=28), db)
    assert r["ok"] and r["after"] == 28


def test_dbopen_exception_fails(tmp_path):
    db = tmp_path / "state.db"
    make_db(db, 25)
    r = dbopen.dbopen(DbUp(fail=True), db)
    assert not r["ok"] and "corrupt" in r["error"]


def test_dbopen_missing_db_is_ok(tmp_path):
    r = dbopen.dbopen(DbUp(), tmp_path / "state.db")
    assert r["ok"] and r["before"] is None


def test_confdiff_orders_and_flattens():
    old = {"_config_version": 27, "model": {"name": "a", "temp": 1}, "gone": 1}
    new = {"_config_version": 30, "model": {"name": "b", "temp": 1}, "fresh": [1, 2]}
    d = confdiff.diff(old, new)
    assert d == {"changed": [["model.name", "a", "b"]], "added": [["fresh", [1, 2]]],
                 "removed": [["gone", 1]]}


def test_confdiff_masks_secrets():
    d = confdiff.diff({"providers": {"x": {"api_key": "sk-1"}}},
                      {"providers": {"x": {"api_key": "sk-2"}}, "bot_token": "t"})
    assert d["changed"] == [["providers.x.api_key", "***", "***"]]
    assert d["added"] == [["bot_token", "***"]]


def test_confdiff_main_writes_result(tmp_path, monkeypatch):
    (tmp_path / "a.yaml").write_text("x: 1\n")
    (tmp_path / "b.yaml").write_text("x: 2\n")
    out = tmp_path / "r.json"
    monkeypatch.setenv("TALARIA_RESULT", str(out))
    confdiff.main([str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml")])
    r = json.loads(out.read_text())
    assert r["ok"] and r["changed"] == [["x", 1, 2]]


def test_confdiff_main_bad_yaml(tmp_path, monkeypatch):
    (tmp_path / "a.yaml").write_text("x: [\n")
    (tmp_path / "b.yaml").write_text("x: 2\n")
    out = tmp_path / "r.json"
    monkeypatch.setenv("TALARIA_RESULT", str(out))
    confdiff.main([str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml")])
    assert not json.loads(out.read_text())["ok"]


def test_dbopen_create_opens_missing(tmp_path):
    class CreatingUp(DbUp):
        def open_state_db(self, path):
            make_db(path, 30)
    r = dbopen.dbopen(CreatingUp(), tmp_path / "state.db", create=True)
    assert r["ok"] and r["before"] is None and r["after"] == 30


# ---- stricter contracts (mutation testing) ----

EMPTY_MIGRATE = {"ok": False, "before": None, "after": None, "latest": None,
                 "messages": [], "error": None}


class BrokenUp(FakeUp):
    def config_versions(self):
        raise RuntimeError("no config module")


def test_migrate_result_shape_when_versions_unreadable():
    assert migrate.migrate(BrokenUp()) == {**EMPTY_MIGRATE, "error": "RuntimeError: no config module"}


def test_migrate_nonzero_exit_result():
    r = migrate.migrate(FakeUp(rc=3))
    assert r == {"ok": False, "before": 27, "after": 30, "latest": 30,
                 "messages": ["✓ step"], "error": "migration exited 3"}


def test_migrate_captures_stderr_too():
    import sys

    class Loud(FakeUp):
        def run_config_migration(self):
            print("warn", file=sys.stderr)
            return super().run_config_migration()

    assert migrate.migrate(Loud())["messages"] == ["warn", "✓ step"]


def test_migrate_keeps_at_most_200_messages():
    class Chatty(FakeUp):
        def run_config_migration(self):
            for i in range(250):
                print(f"line {i}")
            return super().run_config_migration()

    msgs = migrate.migrate(Chatty())["messages"]
    assert len(msgs) == 200 and msgs[-1] == "line 199"


def _fake_upstream(monkeypatch, up):
    import sys
    import types
    mod = types.ModuleType("_upstream")
    for name in ("config_versions", "run_config_migration", "open_state_db", "schema_version"):
        if hasattr(up, name):
            setattr(mod, name, getattr(up, name))
    monkeypatch.setitem(sys.modules, "_upstream", mod)


def test_migrate_main_writes_result(tmp_path, monkeypatch):
    out = tmp_path / "r.json"
    monkeypatch.setenv("TALARIA_RESULT", str(out))
    _fake_upstream(monkeypatch, FakeUp())
    migrate.main()
    assert json.loads(out.read_text()) == {"ok": True, "before": 27, "after": 30, "latest": 30,
                                           "messages": ["✓ step"], "error": None}


def test_dbopen_exception_names_type(tmp_path):
    db = tmp_path / "state.db"
    make_db(db, 25)
    assert dbopen.dbopen(DbUp(fail=True), db)["error"] == "RuntimeError: corrupt"


def test_dbopen_main_uses_hermes_home_and_create_flag(tmp_path, monkeypatch):
    class CreatingUp(DbUp):
        def open_state_db(self, path):
            make_db(path, 30)
    out = tmp_path / "r.json"
    monkeypatch.setenv("TALARIA_RESULT", str(out))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _fake_upstream(monkeypatch, CreatingUp())
    dbopen.main([])
    assert json.loads(out.read_text()) == {"ok": True, "before": None, "after": None,
                                           "schema_version": 30, "error": None}
    assert not (tmp_path / "state.db").exists()
    dbopen.main(["--create"])
    assert json.loads(out.read_text())["after"] == 30 and (tmp_path / "state.db").exists()


def test_dbopen_main_reads_sys_argv(tmp_path, monkeypatch):
    class CreatingUp(DbUp):
        def open_state_db(self, path):
            make_db(path, 30)
    out = tmp_path / "r.json"
    monkeypatch.setenv("TALARIA_RESULT", str(out))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["dbopen.py", "--create"])
    _fake_upstream(monkeypatch, CreatingUp())
    dbopen.main()
    assert json.loads(out.read_text())["after"] == 30


def test_confdiff_main_reads_sys_argv_and_ignores_extra(tmp_path, monkeypatch):
    (tmp_path / "a.yaml").write_text("x: 1\n")
    (tmp_path / "b.yaml").write_text("x: 2\n")
    out = tmp_path / "r.json"
    monkeypatch.setenv("TALARIA_RESULT", str(out))
    monkeypatch.setattr("sys.argv", ["confdiff.py", str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml")])
    confdiff.main()
    assert json.loads(out.read_text())["changed"] == [["x", 1, 2]]
    confdiff.main([str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml"), "extra"])
    assert json.loads(out.read_text())["changed"] == [["x", 1, 2]]


def test_confdiff_main_values_and_empty_files(tmp_path, monkeypatch):
    (tmp_path / "a.yaml").write_text("")
    (tmp_path / "b.yaml").write_text("d: 2020-01-02\n")
    out = tmp_path / "r.json"
    monkeypatch.setenv("TALARIA_RESULT", str(out))
    confdiff.main([str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml")])
    assert json.loads(out.read_text()) == {"ok": True, "error": None, "changed": [],
                                           "added": [["d", "2020-01-02"]], "removed": []}
    (tmp_path / "b.yaml").write_text("")
    confdiff.main([str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml")])
    assert json.loads(out.read_text())["added"] == []


def test_confdiff_main_error_exact(tmp_path, monkeypatch):
    out = tmp_path / "r.json"
    monkeypatch.setenv("TALARIA_RESULT", str(out))
    confdiff.main([str(tmp_path / "missing.yaml"), str(tmp_path / "b.yaml")])
    r = json.loads(out.read_text())
    assert r["error"].startswith("FileNotFoundError: ")
    assert r == {"ok": False, "error": r["error"], "changed": [], "added": [], "removed": []}


def test_confdiff_flatten_empty_dict_is_a_value():
    assert confdiff.flatten({"a": {}, "b": {"c": 1}}) == {"a": {}, "b.c": 1}
    assert confdiff.flatten(None) == {}


@pytest.mark.parametrize("key,masked", [("api_key", True), ("TOKEN", True), ("db.password", True),
                                        ("x.passwd", True), ("secret_x", True),
                                        ("credentials", True), ("keyboard", True),
                                        ("model", False), ("key_path.name", True), ("model.name", False)])
def test_confdiff_mask(key, masked):
    assert (confdiff.mask(key, "v") == "***") is masked


# ---- review I1: masking must cover nested keys, lists and URLs ----

def test_mask_any_segment_and_auth_headers():
    d = confdiff.diff({}, {"api_keys": {"openai": "sk-1"},
                           "headers": {"Authorization": "Bearer x"},
                           "bearer": "b"})
    assert d["added"] == [["api_keys.openai", "***"], ["bearer", "***"],
                          ["headers.Authorization", "***"]]


def test_mask_inside_lists_and_urls():
    new = {"mcp_servers": [{"name": "gh", "env": {"GITHUB_TOKEN": "t", "LEVEL": "1"}}],
           "base_url": "https://user:pw@example.com/v1", "plain": "https://example.com"}
    d = confdiff.diff({}, new)
    assert d["added"] == [
        ["base_url", "https://***@example.com/v1"],
        ["mcp_servers", [{"name": "gh", "env": {"GITHUB_TOKEN": "***", "LEVEL": "1"}}]],
        ["plain", "https://example.com"]]


# ---- v0.2.4: secrets in values under harmless keys ----

@pytest.mark.parametrize("value, masked", [
    (["--api-key", "abc", "--port", "80"], ["--api-key", "***", "--port", "80"]),
    (["-t", "x", "--token=abc"], ["-t", "x", "--token=***"]),
    (["abc", "--api-key"], ["abc", "--api-key"]),
    ("https://x.io/v1?key=abc&mode=fast", "https://x.io/v1?key=***&mode=fast"),
    ("https://x.io/v1?access_token=abc#f", "https://x.io/v1?access_token=***#f"),
    ("sk-or-v1-abcdef123456", "***"),
    ("use ghp_" + "a" * 36 + " here", "use *** here"),
    ("123456789:" + "A" * 35, "***"),
    ("a1" * 20, "***"),
    ("anthropic/claude-sonnet-4-20250514", "anthropic/claude-sonnet-4-20250514"),
    ("https://example.com/v1?mode=fast", "https://example.com/v1?mode=fast"),
    ("x" * 40, "x" * 40),
])
def test_sanitize_values(value, masked):
    assert confdiff.sanitize(value) == masked


def test_changed_values_under_harmless_keys_are_masked():
    d = confdiff.diff({"mcp_servers": {"x": {"args": ["--api-key", "old"]}}},
                      {"mcp_servers": {"x": {"args": ["--api-key", "new"]}}})
    assert d["changed"] == [["mcp_servers.x.args", ["--api-key", "***"], ["--api-key", "***"]]]

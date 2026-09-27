from pathlib import Path

from talaria.conf import load_conf, parse_kv, write_env_value
from talaria.ctx import Paths


def test_parse_kv_ignores_comments_and_trims():
    text = "# c\n a = 1 \n\nb=two words # not a comment\n"
    assert parse_kv(text) == {"a": "1", "b": "two words # not a comment"}


def test_defaults_without_files(tmp_path):
    c = load_conf(Paths(tmp_path))
    assert c.data_dir == tmp_path / "hermes-data"
    assert c.image == "docker.io/nousresearch/hermes-agent"
    assert c.backup_keep == 5
    assert c.backup_exclude == (".cache", ".npm", "home/.cache", "home/.npm", "backups")
    assert c.bind_ip == "127.0.0.1"
    assert c.min_release == "v2026.6.5"
    assert c.settle_seconds == 60
    assert c.registry_tls_verify is True


def test_conf_file_and_env(tmp_path):
    p = Paths(tmp_path)
    p.conf_dir.mkdir(parents=True)
    p.conf_file.write_text(
        "data_dir = ~/data\n"
        "dashboard.bind = tailscale\n"
        "tailscale_ip = 100.64.0.9\n"
        "backup.keep = 3\n"
        "backup.exclude = .cache backups\n"
        "registry_tls_verify = false\n")
    p.env_file.write_text("TALARIA_TELEGRAM_TOKEN=abc\nTALARIA_TELEGRAM_USER_ID=42\n")
    c = load_conf(p)
    assert c.data_dir == tmp_path / "data"
    assert c.bind_ip == "100.64.0.9"
    assert c.backup_keep == 3
    assert c.backup_exclude == (".cache", "backups")
    assert c.registry_tls_verify is False
    assert (c.telegram_token, c.telegram_user_id) == ("abc", 42)


def test_unknown_key_is_an_error(tmp_path):
    p = Paths(tmp_path)
    p.conf_dir.mkdir(parents=True)
    p.conf_file.write_text("nope = 1\n")
    try:
        load_conf(p)
    except ValueError as e:
        assert "nope" in str(e)
    else:
        raise AssertionError


def test_write_env_value_replaces_and_keeps_mode(tmp_path):
    f = tmp_path / ".env"
    write_env_value(f, "A", "1")
    write_env_value(f, "B", "2")
    write_env_value(f, "A", "3")
    assert f.read_text() == "A=3\nB=2\n"
    assert (f.stat().st_mode & 0o777) == 0o600

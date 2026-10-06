import pytest
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
    assert c.release_allow == ()


def test_conf_file_and_env(tmp_path):
    p = Paths(tmp_path)
    p.conf_dir.mkdir(parents=True)
    p.conf_file.write_text(
        "data_dir = ~/data\n"
        "dashboard.bind = tailscale\n"
        "tailscale_ip = 100.64.0.9\n"
        "backup.keep = 3\n"
        "backup.exclude = .cache backups\n"
        "registry_tls_verify = false\n"
        "release_allow = v0.9.9 v0.9.10\n")
    p.env_file.write_text("TALARIA_TELEGRAM_TOKEN=abc\nTALARIA_TELEGRAM_USER_ID=42\n")
    c = load_conf(p)
    assert c.data_dir == tmp_path / "data"
    assert c.bind_ip == "100.64.0.9"
    assert c.backup_keep == 3
    assert c.backup_exclude == (".cache", "backups")
    assert c.registry_tls_verify is False
    assert c.release_allow == ("v0.9.9", "v0.9.10")
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


@pytest.mark.parametrize("keep", ["0", "-1"])
def test_backup_keep_must_be_positive(tmp_path, keep):
    p = Paths(tmp_path)
    p.conf_file.parent.mkdir(parents=True)
    p.conf_file.write_text(f"backup.keep = {keep}\n")
    with pytest.raises(ValueError, match="backup.keep must be at least 1"):
        load_conf(p)


def test_backup_keep_one_is_allowed(tmp_path):
    p = Paths(tmp_path)
    p.conf_file.parent.mkdir(parents=True)
    p.conf_file.write_text("backup.keep = 1\n")
    assert load_conf(p).backup_keep == 1


def test_conf_defaults_follow_the_app(tmp_path):
    p = Paths(tmp_path)
    p.conf_file.parent.mkdir(parents=True)
    p.conf_file.write_text("app = clawvisor\n")
    c = load_conf(p)
    assert (c.dashboard_port, c.repo, c.min_release, c.backup_exclude) == (
        25297, "https://github.com/clawvisor/clawvisor", "v0.9.9", ())
    assert c.data_dir == tmp_path / "clawvisor-data"


def test_hermes_repo_alias_sets_repo(tmp_path):
    p = Paths(tmp_path)
    p.conf_file.parent.mkdir(parents=True)
    p.conf_file.write_text("hermes_repo = https://example/alias\n")
    c = load_conf(p)
    assert c.repo == c.hermes_repo == "https://example/alias"


def test_hermes_repo_is_read_only(tmp_path):
    c = load_conf(Paths(tmp_path))
    with pytest.raises(AttributeError):
        c.hermes_repo = "nope"


def test_repo_and_hermes_repo_together_is_an_error(tmp_path):
    p = Paths(tmp_path)
    p.conf_file.parent.mkdir(parents=True)
    p.conf_file.write_text(
        "repo = https://example/one\nhermes_repo = https://example/two\n")
    with pytest.raises(ValueError, match="set only one of repo / hermes_repo"):
        load_conf(p)

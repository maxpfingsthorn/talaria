import os

import pytest

from talaria import safefs


def test_write_text_replaces_symlink_instead_of_following(tmp_path):
    victim = tmp_path / "victim"; victim.write_text("secret\n")
    link = tmp_path / "data" / ".talaria-schema"; link.parent.mkdir()
    link.symlink_to(victim)
    safefs.write_text(link, "42\n")
    assert victim.read_text() == "secret\n"
    assert not link.is_symlink() and link.read_text() == "42\n"


def test_write_new_refuses_symlink_planted_at_the_tmp_name(tmp_path, monkeypatch):
    victim = tmp_path / "victim"; victim.write_text("x")
    d = tmp_path / "d"; d.mkdir()
    real_open = os.open

    def racing_open(p, flags, *a, **k):
        if str(p).endswith(".tmp") and not os.path.lexists(p):
            os.symlink(victim, p)
        return real_open(p, flags, *a, **k)
    monkeypatch.setattr(safefs.os, "open", racing_open)
    with pytest.raises(OSError):
        safefs.write_text(d / "f", "y")
    assert victim.read_text() == "x"
    assert not (d / "f").exists()
    assert [p.name for p in d.iterdir() if p.name.endswith(".tmp")] == []


def test_write_text_refuses_symlinked_parent(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "data").symlink_to(tmp_path / "real")
    with pytest.raises(ValueError):
        safefs.write_text(tmp_path / "data" / "f", "y")
    assert list((tmp_path / "real").iterdir()) == []


def test_write_text_sets_the_mode(tmp_path):
    safefs.write_text(tmp_path / "a", "1")
    safefs.write_text(tmp_path / "b", "1", mode=0o644)
    assert (tmp_path / "a").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "b").stat().st_mode & 0o777 == 0o644


def test_is_regular(tmp_path):
    f = tmp_path / "f"; f.write_text("1")
    (tmp_path / "l").symlink_to(f)
    assert safefs.is_regular(f) and not safefs.is_regular(tmp_path / "l")
    assert not safefs.is_regular(tmp_path / "missing")
    assert not safefs.is_regular(tmp_path)


def test_copy_regular_copies_content_and_mode(tmp_path):
    s = tmp_path / "s"; s.write_text("data"); s.chmod(0o640)
    assert safefs.copy_regular(s, tmp_path / "out") == str(tmp_path / "out")
    assert (tmp_path / "out").read_text() == "data"
    assert (tmp_path / "out").stat().st_mode & 0o777 == 0o640


def test_copy_regular_refuses_symlink(tmp_path):
    (tmp_path / "t").write_text("host secret")
    (tmp_path / "s").symlink_to(tmp_path / "t")
    with pytest.raises(OSError):
        safefs.copy_regular(tmp_path / "s", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_copy_regular_refuses_non_regular(tmp_path):
    with pytest.raises(OSError):
        safefs.copy_regular(tmp_path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_copy_regular_refuses_a_planted_destination_link(tmp_path):
    (tmp_path / "s").write_text("new")
    (tmp_path / "victim").write_text("keep")
    (tmp_path / "out").symlink_to(tmp_path / "victim")
    with pytest.raises(OSError):
        safefs.copy_regular(tmp_path / "s", tmp_path / "out")
    assert (tmp_path / "victim").read_text() == "keep"


def test_write_text_mode_survives_a_restrictive_umask_without_path_chmod(tmp_path, monkeypatch):
    def no_chmod(*a, **k):
        raise AssertionError("path-based chmod")
    monkeypatch.setattr(safefs.os, "chmod", no_chmod)
    old = os.umask(0o077)
    try:
        safefs.write_text(tmp_path / "f", "1", mode=0o644)
    finally:
        os.umask(old)
    assert (tmp_path / "f").stat().st_mode & 0o777 == 0o644

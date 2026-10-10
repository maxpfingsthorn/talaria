import pytest


@pytest.fixture(autouse=True)
def _login_home(tmp_path, monkeypatch):
    """Setup's operator check reads the login's own home: never the runner's real one."""
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path / "opshome"))

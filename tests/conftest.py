import pytest


@pytest.fixture(autouse=True)
def _login_home(tmp_path, monkeypatch):
    """Setup's operator check reads the login's own home: never the runner's real one."""
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path / "opshome"))


@pytest.fixture(autouse=True)
def _no_trust_check(request, monkeypatch):
    """Unit tests of callers don't build signed repos: the signature check is a no-op unless
    the test is marked `real_trust` (a refusal is simulated by patching in a raising `check`)."""
    if request.node.get_closest_marker("real_trust"):
        return
    monkeypatch.setattr("talaria.trust.check", lambda *a, **k: None)

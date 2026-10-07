"""Spec §7.5: a v0.4 app install self-updated to v0.5 keeps its own bot until it is moved,
running the hub code with a local executor."""
from talaria import cli, hubexec, setup, units
from talaria.ctx import Paths
from tests.test_setup import args, svc  # noqa: F401  (fixture)

V04_ENV = "TALARIA_TELEGRAM_TOKEN=1:" + "a" * 35 + "\nTALARIA_TELEGRAM_USER_ID=42\n"


def v04_units(p):
    p.units_dir.mkdir(parents=True, exist_ok=True)
    for name in units.TALARIA_UNITS:
        (p.units_dir / name).write_text(f"v0.4 {name}\n")


def test_v04_bot_and_timer_run_the_hub_code_locally(tmp_path, monkeypatch):
    p = Paths(tmp_path)
    p.conf_dir.mkdir(parents=True)
    p.conf_file.write_text("data_dir = ~/hermes-data\n")
    p.env_file.write_text(V04_ENV)
    v04_units(p)
    seen = []
    from talaria import hubcheck, telegram
    monkeypatch.setattr(telegram, "run", lambda h: seen.append(
        ("bot", h.transitional, h.apps["hermes"].executor.argv(["hello"]))) or 0)
    monkeypatch.setattr(hubcheck, "check", lambda h, timer: seen.append(
        ("check", h.transitional, timer)) or 0)
    load = lambda: hubexec.load_hub(tmp_path)
    assert cli.main(["bot"], make_hub=load) == 0
    assert cli.main(["check", "--timer"], make_hub=load) == 0
    assert seen == [("bot", True, [str(tmp_path / ".local/bin/talaria"), "op", "hello"]),
                    ("check", True, True)]


def test_app_phase_leaves_the_v04_bot_alone(svc, capsys):  # noqa: F811
    p = svc.paths
    v04_units(p)
    p.conf_dir.mkdir(parents=True, exist_ok=True)
    p.env_file.write_text(V04_ENV)
    assert setup.service_phase(svc, args(as_service=True)) == 0
    assert all((p.units_dir / n).read_text() == f"v0.4 {n}\n" for n in units.TALARIA_UNITS)
    assert p.env_file.read_text() == V04_ENV
    assert not [c for c in svc.sh.calls if any("talaria-" in a for a in c)]

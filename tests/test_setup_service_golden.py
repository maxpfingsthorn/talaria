"""Exact output, commands and files of the service phase, per branch."""
import pytest

from talaria import lock, setup, state
from talaria.adopt import Found, Plan
from talaria.conf import parse_kv
from tests.test_setup import args, svc  # noqa: F401  (fixture)

H = "~H"
NEW = "OK: no existing Hermes found: fresh install\n"
PW = f"OK: dashboard password generated in {H}/.config/talaria/hermes.env (user admin)\n"
PAIR = ("ACTION REQUIRED: in a private chat with your bot, send within 15 minutes:\n"
        "  /pair CODE2345\n")
PAIRED = "OK: paired with Ann (@ann)\n"
PULLED = "OK: Hermes v2026.9.24 pulled and verified\n"
RUNNING = (f"OK: Hermes is running. Dashboard: http://127.0.0.1:9119 (user admin, "
           f"password in {H}/.config/talaria/hermes.env)\nDONE\n")
UNITS = [(["systemctl", "--user", "daemon-reload"], None),
         (["systemctl", "--user", "enable", "--now", "talaria-check.timer",
           "talaria-telegram.service"], None),
         (["systemctl", "--user", "restart", "talaria-telegram.service"], None),
         (["podman", "tag", "sha256:n", "localhost/hermes-agent:current"], None)]
RESTART = [(["systemctl", "--user", "stop", "hermes.service"], 600),
           (["systemctl", "--user", "reset-failed", "hermes.service"], 600),
           (["systemctl", "--user", "start", "hermes.service"], 600)]


@pytest.fixture
def s(svc, monkeypatch):  # noqa: F811
    monkeypatch.setattr(setup.telegram, "new_code", lambda: "CODE2345")
    monkeypatch.setattr(setup, "which", lambda t: None if t == "tailscale" else f"/usr/bin/{t}")
    monkeypatch.setattr(setup.getpass, "getuser", lambda: "hermes")
    return svc


def run(ctx, capsys, **a):
    rc = setup.service_phase(ctx, args(as_service=True, **a))
    out = capsys.readouterr().out.replace(str(ctx.paths.home), H)
    return rc, out, list(zip(ctx.sh.calls, ctx.sh.timeouts))


def test_fresh_exact(s, capsys):
    rc, out, cmds = run(s, capsys)
    assert out == NEW + PW + PAIR + PAIRED + PULLED + RUNNING
    assert cmds == UNITS + RESTART and rc == 0
    assert s.paths.conf_file.read_text() == ("# Talaria settings; see README.\n"
                                             "data_dir = ~/hermes-data\n")
    assert s.paths.env_file.read_text() == "TALARIA_TELEGRAM_USER_ID=42\n"
    assert (s.paths.conf_dir.stat().st_mode & 0o777) == 0o700
    assert (s.paths.state_dir.stat().st_mode & 0o777) == 0o700
    assert s.conf.data_dir.is_dir()
    assert state.load(s.paths)["current"] == {"tag": "v2026.9.24", "id": "sha256:n",
                                              "ref": "r", "digest": "d"}


def test_existing_conf_is_kept(s, capsys):
    s.paths.conf_dir.mkdir(parents=True)
    s.paths.conf_file.write_text("check.time = 03:00\n")
    run(s, capsys)
    assert s.paths.conf_file.read_text() == "check.time = 03:00\n"


def test_tailscale_available_hint(s, monkeypatch, capsys):
    monkeypatch.setattr(setup, "which", lambda t: f"/usr/bin/{t}")
    rc, out, cmds = run(s, capsys)
    assert out == (NEW + PW + PAIR + PAIRED +
                   "OK: Tailscale found: set dashboard.bind = tailscale in talaria.conf and run "
                   "setup again to reach the dashboard over your tailnet\n" + PULLED + RUNNING)


def test_tailscale_bind_records_address(s, capsys):
    s.conf.dashboard_bind = "tailscale"
    s.sh.on("tailscale", "ip", "-4", out="100.64.1.2\nfd7a::1\n")
    rc, out, cmds = run(s, capsys)
    assert cmds == [(["tailscale", "ip", "-4"], None)] + UNITS + RESTART
    assert parse_kv(s.paths.conf_file.read_text())["tailscale_ip"] == "100.64.1.2"
    assert "Dashboard: http://100.64.1.2:9119 " in out
    assert "Tailscale found" not in out


def test_tailscale_ip_already_known(s, capsys):
    s.conf.dashboard_bind, s.conf.tailscale_ip = "tailscale", "100.64.9.9"
    rc, out, cmds = run(s, capsys)
    assert not s.sh.called("tailscale") and "http://100.64.9.9:9119" in out


def test_no_token_exact(s, capsys):
    s.conf.telegram_token = ""
    rc, out, cmds = run(s, capsys)
    assert rc == 10 and cmds == []
    assert out == NEW + PW + (
        "ACTION REQUIRED: create a Telegram bot: open @BotFather, send /newbot, copy the token. "
        "Then, in your own terminal (not through an agent), run:\n"
        f"  sudo -u hermes -H {H}/.local/bin/talaria set-token\n")


def test_pair_fail_exact(s, monkeypatch, capsys):
    monkeypatch.setattr(setup.telegram, "pair",
                        lambda c, api, code, timeout_s=900, announce=lambda: None: (announce(), None)[1])
    rc, out, cmds = run(s, capsys)
    assert rc == 10 and cmds == []
    assert out == NEW + PW + PAIR + "STOP: no /pair message arrived; run setup again for a new code\n"
    assert not s.paths.env_file.exists()


def test_pair_uses_configured_api(s, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(setup.telegram, "TelegramAPI", lambda base, token: seen.append((base, token)))
    run(s, capsys)
    assert seen == [(s.conf.telegram_api, s.conf.telegram_token)]


def test_paired_user_without_username(s, monkeypatch, capsys):
    monkeypatch.setattr(setup.telegram, "pair",
                        lambda c, api, code, timeout_s=900, announce=lambda: None: {"id": 7})
    rc, out, cmds = run(s, capsys)
    assert "OK: paired with  (@-)\n" in out


def test_already_paired_skips_pairing(s, monkeypatch, capsys):
    s.conf.telegram_user_id = 42
    monkeypatch.setattr(setup.telegram, "pair", lambda *a, **k: pytest.fail("paired again"))
    rc, out, cmds = run(s, capsys)
    assert rc == 0 and "/pair" not in out


def test_plan_exact(s, capsys):
    rc, out, cmds = run(s, capsys, plan=True)
    assert rc == 0 and cmds == []
    assert out == NEW + ("PLAN: dashboard password, Telegram token and pairing, install units, "
                         "start Hermes, verify\n")
    assert not s.paths.hermes_env.exists()


def test_check_fail_exact(s, monkeypatch, capsys):
    monkeypatch.setattr(setup.service, "post_start_check", lambda c: "hermes.service restarted")
    rc, out, cmds = run(s, capsys)
    assert rc == 1 and cmds == UNITS + RESTART
    assert out == NEW + PW + PAIR + PAIRED + PULLED + (
        "STOP: Hermes did not come up: hermes.service restarted\n")


def test_no_release_exact(s, monkeypatch, capsys):
    monkeypatch.setattr(s.app, "published", lambda c, tags: set())
    rc, out, cmds = run(s, capsys)
    assert rc == 1 and cmds == []
    assert out == NEW + PW + PAIR + PAIRED + "STOP: no Hermes release image found\n"


def test_fresh_image_respects_floor_and_tls(s, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(s.app, "releases",
                        lambda c: seen.setdefault("repo", c.conf.hermes_repo) and
                        {"v2026.5.1": "a", "v2026.9.24": "c"})
    monkeypatch.setattr(s.app, "published",
                        lambda c, tags: (seen.update(image=c.conf.image, tls=c.conf.registry_tls_verify),
                                         {"v2026.5.1", "v2026.9.24"})[1])
    pulled = []
    monkeypatch.setattr(s.app, "fetch",
                        lambda c, t, commit: (pulled.append((t, commit)),
                                              {"tag": t, "id": "sha256:n"})[1])
    s.conf.registry_tls_verify = False
    run(s, capsys)
    assert seen == {"repo": s.conf.hermes_repo, "image": s.conf.image, "tls": False}
    assert pulled == [("v2026.9.24", "c")]


def test_fresh_image_below_floor_only(s, monkeypatch, capsys):
    monkeypatch.setattr(s.app, "releases", lambda c: {"v2026.5.1": "a"})
    monkeypatch.setattr(s.app, "published", lambda c, tags: {"v2026.5.1"})
    rc, out, cmds = run(s, capsys)
    assert rc == 1 and "STOP: no Hermes release image found" in out


def test_adopt_unknown_exact(s, capsys):
    rc, out, cmds = run(s, capsys, adopt="x.service")
    assert (rc, out, cmds) == (1, "STOP: no Hermes install with unit x.service\n", [])


def test_fresh_and_adopt_texts_name_the_app(s, monkeypatch, capsys):
    monkeypatch.setattr(type(s.app), "title", "Demo")
    monkeypatch.setattr(s.app, "published", lambda c, tags: set())
    rc, out, cmds = run(s, capsys)
    assert rc == 1 and out == NEW.replace("Hermes", "Demo") + PW + PAIR + PAIRED + \
        "STOP: no Demo release image found\n"


def test_rerun_unchanged_running_does_not_restart(s, capsys):
    run(s, capsys)
    s.sh.calls.clear()
    s.sh.timeouts.clear()
    s.sh.on("systemctl", "--user", "is-active", out="active\n")
    rc, out, cmds = run(s, capsys)
    assert rc == 0 and out == RUNNING
    assert cmds == UNITS + [(["systemctl", "--user", "is-active", "hermes.service"], 600)]


def test_rerun_stopped_hermes_is_started(s, capsys):
    run(s, capsys)
    s.sh.calls.clear()
    s.sh.timeouts.clear()
    rc, out, cmds = run(s, capsys)
    assert cmds == UNITS + [(["systemctl", "--user", "is-active", "hermes.service"], 600)] + RESTART


def test_rerun_with_changed_quadlet_restarts(s, capsys):
    run(s, capsys)
    s.paths.quadlet.write_text("# Managed by Talaria. older version\n")
    s.sh.on("systemctl", "--user", "is-active", out="active\n")
    s.sh.calls.clear()
    s.sh.timeouts.clear()
    rc, out, cmds = run(s, capsys)
    assert cmds == UNITS + RESTART


def test_busy_lock_stops(s, capsys):
    s.conf.telegram_user_id = 42
    with lock.op_lock(s.paths):
        rc, out, cmds = run(s, capsys)
    assert rc == 1 and out.endswith("STOP: a Talaria operation is running; run setup again in a minute\n")
    assert cmds == []


# ---- adoption branches (the adopt module itself is tested in test_adopt.py) ----

def found(unit="old.service"):
    return Found(unit=unit, container="c1", name="old", image_id="sha256:o", mounts=[], env={},
                 quadlet=None)


def adopt_env(monkeypatch, s, problems=(), apply_rc=0):
    f = found()
    p = Plan(f, {"tag": "v2026.8.3", "id": "sha256:n"}, s.paths.home / "data", {}, [],
             list(problems), "DIFF")
    calls = []
    monkeypatch.setattr(setup.adopt, "detect", lambda c: [f])
    monkeypatch.setattr(setup.adopt, "plan", lambda c, ff: (calls.append("plan"), p)[1])
    monkeypatch.setattr(setup.adopt, "print_plan", lambda pp, env: print("PLAN-SHOWN"))

    def apply(c, ff, pp):
        calls.append("apply")
        st = state.load(c.paths)
        st["current"] = {"tag": "v2026.8.3", "id": "sha256:n"}
        state.save(c.paths, st)
        c.paths.conf_file.write_text("data_dir = ~/data\n")
        return apply_rc

    monkeypatch.setattr(setup.adopt, "apply", apply)
    monkeypatch.setattr(setup.adopt, "manual_steps", lambda c, pp: "MANUAL-STEPS")
    return calls


def test_adopt_needs_explicit_flag(s, monkeypatch, capsys):
    calls = adopt_env(monkeypatch, s)
    rc, out, cmds = run(s, capsys)
    assert rc == 10 and calls == ["plan"] and cmds == []
    assert out == ("PLAN-SHOWN\nACTION REQUIRED: adopting stops and restarts the running agent. "
                   "Review the diff above, then run: talaria setup --adopt old.service\n")


def test_adopt_plan_mode_returns_zero(s, monkeypatch, capsys):
    adopt_env(monkeypatch, s)
    rc, out, cmds = run(s, capsys, plan=True)
    assert rc == 0


def test_adopt_problems_stop(s, monkeypatch, capsys):
    adopt_env(monkeypatch, s, problems=["bad mount", "too old"])
    rc, out, cmds = run(s, capsys, adopt="old.service")
    assert rc == 1 and out == "PLAN-SHOWN\nSTOP: bad mount\nSTOP: too old\n"


def test_adopt_applies_and_reloads_conf(s, monkeypatch, capsys):
    calls = adopt_env(monkeypatch, s)
    rc, out, cmds = run(s, capsys, adopt="old.service")
    assert rc == 0 and calls == ["plan", "apply"]
    assert s.conf.data_dir == s.paths.home / "data"     # conf reloaded after apply
    assert f"Volume={s.paths.home / 'data'}:/opt/data" in s.paths.quadlet.read_text()
    assert "pulled and verified" not in out


def test_adopt_apply_failure_stops(s, monkeypatch, capsys):
    adopt_env(monkeypatch, s, apply_rc=1)
    rc, out, cmds = run(s, capsys, adopt="old.service")
    assert rc == 1 and cmds == []


def test_adopt_check_failure_prints_manual_steps(s, monkeypatch, capsys):
    adopt_env(monkeypatch, s)
    monkeypatch.setattr(setup.service, "post_start_check", lambda c: "no status")
    rc, out, cmds = run(s, capsys, adopt="old.service")
    assert rc == 1 and out.endswith("STOP: Hermes did not come up: no status\nMANUAL-STEPS\n")


def test_several_installs_exact(s, monkeypatch, capsys):
    monkeypatch.setattr(setup.adopt, "detect", lambda c: [found("a.service"), found("b.service")])
    rc, out, cmds = run(s, capsys)
    assert rc == 1 and out == ("FOUND: Hermes unit a.service (container old)\n"
                               "FOUND: Hermes unit b.service (container old)\n"
                               "STOP: several Hermes installs; choose one with --adopt UNIT\n")


def test_adopt_flag_selects_one_of_several(s, monkeypatch, capsys):
    a, b = found("a.service"), found("b.service")
    monkeypatch.setattr(setup.adopt, "detect", lambda c: [a, b])
    chosen = []
    monkeypatch.setattr(setup.adopt, "plan", lambda c, f: (chosen.append(f.unit),
                                                           Plan(f, None, None, {}, [], ["x"], ""))[1])
    monkeypatch.setattr(setup.adopt, "print_plan", lambda p, env: None)
    run(s, capsys, adopt="b.service")
    assert chosen == ["b.service"]


def test_managed_install_skips_detection(s, monkeypatch, capsys):
    run(s, capsys)
    monkeypatch.setattr(setup.adopt, "detect", lambda c: pytest.fail("detected again"))
    rc, out, cmds = run(s, capsys)
    assert rc == 0


# ---- review C2 ----

def test_foreign_hermes_container_stops_fresh_install(s, capsys):
    s.paths.quadlet_dir.mkdir(parents=True)
    s.paths.quadlet.write_text("[Container]\nImage=mine\n")
    rc, out, cmds = run(s, capsys)
    assert rc == 1 and cmds == []
    assert out == (f"STOP: {H}/.config/containers/systemd/hermes.container exists but was not "
                   "written by Talaria; start that Hermes and run setup again to adopt it, or "
                   "move the file away\n")
    assert s.paths.quadlet.read_text() == "[Container]\nImage=mine\n"


def test_stopped_quadlet_install_stops(s, monkeypatch, capsys):
    s.paths.quadlet_dir.mkdir(parents=True)
    q = s.paths.quadlet_dir / "old.container"
    q.write_text("[Container]\nVolume=/d:/opt/data\n")
    rc, out, cmds = run(s, capsys)
    assert rc == 1
    assert out == (f"STOP: found a Hermes Quadlet that is not running: {H}/.config/containers/"
                   "systemd/old.container; start it (systemctl --user start old.service) and run "
                   "setup again\n")


def test_fresh_refuses_mount_point_data_dir(s, monkeypatch, capsys):
    monkeypatch.setattr(setup, "renamable", lambda p: False)
    s.conf.telegram_user_id = 42
    rc, out, cmds = run(s, capsys)
    assert rc == 1 and out.endswith(
        f"STOP: the data dir {H}/hermes-data is a mount point or on another filesystem than its "
        "parent; Talaria restores by renaming it, so it must be a plain directory\n")


def test_tailscale_address_resolved_before_adoption_plan(s, monkeypatch, capsys):
    s.conf.dashboard_bind = "tailscale"
    s.sh.on("tailscale", "ip", "-4", out="100.64.1.2\n")
    seen = []
    monkeypatch.setattr(setup.adopt, "detect", lambda c: (seen.append(c.conf.tailscale_ip), [])[1])
    run(s, capsys, plan=True)
    assert seen == ["100.64.1.2"]

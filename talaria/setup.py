from __future__ import annotations

import getpass
import os
import pwd
import re
import shutil
import subprocess
import sys
from pathlib import Path

from talaria import adopt, apps, images, lock, rehearse, service, state, telegram, units
from talaria.conf import check_bind, load_conf, parse_kv, write_env_value
from talaria.disk import NOT_RENAMABLE, renamable
from talaria.hubconf import NAME_RE, load_hub_conf, register_app
from talaria.notify import ApiError, TelegramAPI
from talaria.state import ensure_dir
from talaria.tags import pick_candidate, releases

REPO = Path(__file__).resolve().parent.parent
TOKEN_RE = re.compile(r"^\d{3,}:[A-Za-z0-9_-]{30,}$")
GITHUB_SSH = re.compile(r"^(?:git@github\.com:|ssh://git@github\.com(?::\d+)?/)(.+?)(?:\.git)?/?$")
TOOLS = {  # tool: (Debian/Ubuntu, Fedora/RHEL, Arch)
    "podman": ("podman", "podman", "podman"), "git": ("git", "git", "git"),
    "tar": ("tar", "tar", "tar"), "gzip": ("gzip", "gzip", "gzip"),
    "systemd-run": ("systemd", "systemd", "systemd"), "loginctl": ("systemd", "systemd", "systemd"),
    "sudo": ("sudo", "sudo", "sudo"),
}
which = shutil.which


def say(kind: str, text: str) -> None:
    print(f"{kind}: {text}", flush=True)


def prerequisites(sh) -> list[str]:
    missing = []
    for tool, (deb, fed, arch) in TOOLS.items():
        if which(tool) is None:
            missing.append(tool)
            say("MISSING", tool)
            print(f"  hint: Debian/Ubuntu: apt install {deb} · Fedora/RHEL: dnf install {fed}"
                  f" · Arch: pacman -S {arch}")
    if "podman" not in missing:
        m = re.search(r"(\d+)\.(\d+)", sh.run(["podman", "--version"]).stdout)
        if not m or (int(m[1]), int(m[2])) < (4, 9):
            missing.append("podman")
            say("MISSING", f"podman >= 4.9 (found {m[0] if m else 'unknown'})")
    return missing


def selinux_enforcing(sh) -> bool:
    return bool(which("getenforce")) and \
        sh.run(["getenforce"], check=False).stdout.strip() == "Enforcing"


HUB = "talaria"
HUB_CONF_HEAD = "# Talaria hub settings; see README.\n"
PASTE = "paste this into your terminal (sudo asks for your password), then run setup again"


def _sudoers(rule: str, name: str) -> list[str]:
    """Validate with visudo before it can break sudo; never written in place."""
    return ["tmp=$(mktemp)", f"echo {rule} > \"$tmp\"", "visudo -cf \"$tmp\"",
            f"install -m 440 -o root -g root \"$tmp\" /etc/sudoers.d/{name}", "rm -f \"$tmp\""]


def account_lines(user: str, operator: str, create: bool) -> list[str]:
    lines = []
    if create:
        lines += [f"id {user} >/dev/null 2>&1 || useradd --create-home --shell /bin/bash {user}",
                  f"grep -q '^{user}:' /etc/subuid || echo 'WARNING: {user} has no subuid range; see README'"]
    return lines + [f"loginctl enable-linger {user}",
                    *_sudoers(f"'{operator} ALL=({user}) NOPASSWD: ALL'", f"talaria-{user}")]


def op_rule_lines(hub: str, user: str) -> list[str]:
    """The hub may run `talaria op …` as the app and nothing else (spec §4.1). The app's
    home comes from the passwd database when the block runs."""
    return [f"home=$(getent passwd {user} | cut -d: -f6)", "test -n \"$home\"",
            *_sudoers(f"\"{hub} ALL=({user}) NOPASSWD: $home/.local/bin/talaria op *\"",
                      f"talaria-{hub}-{user}")]


def root_block(lines: list[str]) -> str:
    """One command the person pastes whole into their own terminal; sudo asks for the
    password. The quoted heredoc delimiter keeps their shell from expanding anything."""
    return "\n".join(["sudo bash -euo pipefail <<'TALARIA'", *lines,
                      "echo 'Talaria: root step done'", "TALARIA"])


def sudo_as(user: str, home: str) -> list[str]:
    # sudo may keep the caller's XDG_* dirs (e.g. on CI runners); podman and git must
    # use the service user's own
    return ["sudo", "-n", "-u", user, "-H", "env", "-u", "XDG_CONFIG_HOME", "-u",
            "XDG_DATA_HOME", "-u", "XDG_STATE_HOME", "-u", "XDG_CACHE_HOME", f"HOME={home}"]


def _bus(pw) -> list[str]:
    return [f"XDG_RUNTIME_DIR=/run/user/{pw.pw_uid}",
            f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{pw.pw_uid}/bus"]


def _pw(getpwnam, name):
    try:
        return getpwnam(name)
    except KeyError:
        return None


def _can_sudo(sh, user: str) -> bool:
    return sh.run(["sudo", "-n", "-u", user, "true"], check=False).returncode == 0


def _installed(sh, user: str, home: str) -> bool:
    return sh.run(["sudo", "-n", "-u", user, "test", "-e", f"{home}/.local/share/talaria"],
                  check=False).returncode == 0


def _install(sh, user: str, home: str, url: str, ref: str, installed: bool) -> None:
    install, sudo = f"{home}/.local/share/talaria", sudo_as(user, home)
    if not installed:
        sh.run(sudo + ["git", "clone", "-q", url, install], timeout=600)
    sh.run(sudo + ["git", "-C", install, "fetch", "-q", "--tags", "origin"], timeout=600)
    sh.run(sudo + ["git", "-C", install, "checkout", "-q", ref])
    sh.run(sudo + ["mkdir", "-p", f"{home}/.local/bin"])
    sh.run(sudo + ["ln", "-sfn", f"{install}/bin/talaria", f"{home}/.local/bin/talaria"])
    say("OK", f"Talaria {ref} installed for {user}")


def install_url(url: str) -> str | None:
    """The service user has no SSH key: turn a GitHub SSH remote into https. None if the
    remote needs SSH otherwise."""
    m = GITHUB_SSH.match(url)
    if m:
        return f"https://github.com/{m[1]}"
    if re.match(r"^(ssh://|[\w.-]+@)", url):
        return None
    return re.sub(r"^(https?://)[^/@]*@", r"\1", url)   # never copy credentials along


OLD_UNITS = ("talaria-telegram.service", "talaria-check.timer", "talaria-check.service")
TG_LINES = "^TALARIA_TELEGRAM_(TOKEN|USER_ID)="


def move_env(read_argv: list[str], write_argv: list[str], popen=subprocess.Popen) -> int:
    """Pipe one process's stdout straight into another's stdin. Used for the bot token: it
    never passes through this process, an argv or the terminal."""
    reader = popen(read_argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    writer = popen(write_argv, stdin=reader.stdout)
    reader.stdout.close()          # the writer holds the only read end now
    wrc, rrc = writer.wait(), reader.wait()
    return 0 if wrc == 0 and rrc == 0 else 1


def _v04_bot(sh, user: str, home: str) -> tuple[bool, bool]:
    """(own bot unit, own bot token): what a v0.4 install has and a v0.5 app must not."""
    s = sudo_as(user, home)
    has_units = sh.run(s + ["test", "-e", f"{home}/.config/systemd/user/talaria-telegram.service"],
                       check=False).returncode == 0
    has_token = sh.run(s + ["grep", "-qs", "^TALARIA_TELEGRAM_", f"{home}/.config/talaria/.env"],
                       check=False).returncode == 0
    return has_units, has_token


def _migrate(sh, user: str, pw, hub: str, hub_pw, found, move) -> int:
    """Spec §7.4, between the app's and the hub's setup: one bot per host. Each step is
    idempotent, so a re-run after an interruption resumes. The app's Quadlet and service
    are never touched."""
    has_units, has_token = found
    home, s = pw.pw_dir, sudo_as(user, pw.pw_dir)
    if has_units:
        sh.run(s + _bus(pw) + ["systemctl", "--user", "disable", "--now",
                               "talaria-telegram.service", "talaria-check.timer"], check=False)
        sh.run(s + ["rm", "-f", *(f"{home}/.config/systemd/user/{u}" for u in OLD_UNITS)])
        sh.run(s + _bus(pw) + ["systemctl", "--user", "daemon-reload"])
        say("OK", f"stopped {user}'s own bot; the hub {hub} runs the only one")
    if has_token:
        env = f"{home}/.config/talaria/.env"
        rc = move(s + ["grep", "-E", TG_LINES, env],
                  sudo_as(hub, hub_pw.pw_dir) + [f"{hub_pw.pw_dir}/.local/bin/talaria", "setup",
                                                 "--as-hub", "--import-telegram"])
        if rc != 0:
            say("STOP", f"could not move the bot token from {user} to {hub}; run setup again")
            return 1
        sh.run(s + ["sed", "-i", "-E", f"/{TG_LINES}/d", env])
        say("OK", f"{user} holds no bot token any more")
    return 0


def import_telegram(ctx, stream) -> int:
    """Spec §7.4 step 2, the hub's end of the pipe: the app's Telegram lines on stdin."""
    kv = parse_kv(stream.read())
    token, uid = kv.get("TALARIA_TELEGRAM_TOKEN", ""), kv.get("TALARIA_TELEGRAM_USER_ID", "")
    if not TOKEN_RE.match(token):
        say("STOP", "no valid bot token on stdin; nothing changed")
        return 1
    if load_hub_conf(ctx.paths).telegram_token:
        say("OK", "the hub already has a bot; the app's own token is not needed any more")
        return 0
    write_env_value(ctx.paths.env_file, "TALARIA_TELEGRAM_TOKEN", token)
    if uid.isdigit():
        write_env_value(ctx.paths.env_file, "TALARIA_TELEGRAM_USER_ID", uid)
        say("OK", "bot token and owner moved to the hub (same bot, no new pairing)")
    else:
        say("OK", "bot token moved to the hub; pair it when setup asks")
    return 0


def operator_phase(sh, args, *, getpwnam=pwd.getpwnam, operator=None, call=subprocess.call,
                   linger_dir=Path("/var/lib/systemd/linger"), app="hermes",
                   explicit_app=True, move=None) -> int:
    if app not in apps.NAMES:
        say("STOP", f"unknown app: {app!r}; choose one of {', '.join(apps.NAMES)}")
        return 1
    A = apps.get(app)
    operator = operator or getpass.getuser()
    user = args.user or app
    hub = getattr(args, "hub", None) or HUB
    for name in (user, operator, hub):
        if not NAME_RE.match(name):
            say("STOP", f"not a valid account name: {name!r}")
            return 1
    if user == hub:
        say("STOP", f"{hub} is the hub's account; the app needs an account of its own (--user)")
        return 1
    if prerequisites(sh):
        return 10
    if selinux_enforcing(sh):
        say("STOP", "SELinux is enforcing; Talaria v1 does not support that")
        return 1
    lingers = lambda name: (Path(linger_dir) / name).exists()
    pw = _pw(getpwnam, user)
    app_lines, installed = [], False
    if pw is None:
        if args.plan:
            say("PLAN", f"create the account {user}: setup prints a block to run as root")
            say("PLAN", f"then run setup again with --user {user} to install Talaria for it")
            return 0
        app_lines = account_lines(user, operator, create=True)
    else:
        sudo_ok = _can_sudo(sh, user)
        installed = sudo_ok and _installed(sh, user, pw.pw_dir)
        if not args.user and not installed:
            say("FOUND", f"account {user} exists but Talaria is not installed for it")
            say("STOP", f"confirm with the person, then re-run with --user {user}")
            return 1
        if not sudo_ok or not lingers(user):
            app_lines = account_lines(user, operator, create=False)
    hub_pw = _pw(getpwnam, hub)
    hub_lines, hub_installed = [], False
    if hub_pw is None:
        hub_lines = account_lines(hub, operator, create=True)
    else:
        hub_ok = _can_sudo(sh, hub)
        hub_installed = hub_ok and _installed(sh, hub, hub_pw.pw_dir)
        if not hub_ok or not lingers(hub):
            hub_lines = account_lines(hub, operator, create=False)
    if app_lines or hub_lines:
        if args.plan:
            say("PLAN", f"accounts, linger and sudo rules for {hub} and {user}: setup prints "
                        "a block to run as root")
            return 0
        again = f" with --user {user}" if pw is None else ""
        say("ACTION REQUIRED", f"{PASTE}{again}:\n"
            + root_block(hub_lines + app_lines + op_rule_lines(hub, user)))
        return 10
    tag = sh.run(["git", "-C", str(REPO), "describe", "--tags", "--exact-match"], check=False)
    if tag.returncode == 0:
        ref = tag.stdout.strip()
    elif args.dev:
        ref = sh.run(["git", "-C", str(REPO), "rev-parse", "HEAD"]).stdout.strip()
    else:
        say("STOP", "this checkout is not at a release tag; check out the latest tag "
                    "(or pass --dev)")
        return 1
    url = str(REPO) if args.dev else install_url(
        sh.run(["git", "-C", str(REPO), "remote", "get-url", "origin"],
               check=False).stdout.strip() or str(REPO))
    if url is None:
        say("STOP", "this checkout's origin needs SSH, but the service user has no key; "
                    "clone Talaria over https")
        return 1
    found = _v04_bot(sh, user, pw.pw_dir)
    if args.plan:
        say("PLAN", f"install Talaria {ref} for {user} from {url}")
        say("PLAN", f"then: detect {A.title} (fresh or adopt), {A.prepare_summary}, units, "
                    f"start {A.title}, verify")
        say("PLAN", f"then: install the same Talaria for the hub {hub} and register {app} "
                    "with it (Telegram bot token and pairing, once per host)")
        if any(found):
            say("PLAN", f"move the bot from {user} to the hub {hub} (same bot, same chat, no "
                        f"new pairing; {A.title} is not restarted)")
        return 0
    home, hub_home = pw.pw_dir, hub_pw.pw_dir
    _install(sh, user, home, url, ref, installed)
    _install(sh, hub, hub_home, url, ref, hub_installed)
    probe = sh.run(["sudo", "-n", "-u", hub, "sudo", "-n", "-H", "-u", user,
                    f"{home}/.local/bin/talaria", "op", "hello"], check=False)
    if probe.returncode != 0:
        if probe.stderr.lstrip().startswith("sudo:"):
            say("ACTION REQUIRED", f"{PASTE}:\n" + root_block(op_rule_lines(hub, user)))
            return 10
        say("STOP", f"Talaria for {user} does not answer: "
                    f"{(probe.stderr or probe.stdout).strip()[-300:]}")
        return 1
    rest = (["--app", app] if explicit_app else []) + \
        (["--adopt", args.adopt] if args.adopt else [])
    rc = call(sudo_as(user, home) + _bus(pw)
              + [f"{home}/.local/bin/talaria", "setup", "--as-service", *rest])
    if rc != 0:
        return rc
    if any(found):
        rc = _migrate(sh, user, pw, hub, hub_pw, found, move or move_env)
        if rc != 0:
            return rc
    rc = call(sudo_as(hub, hub_home) + _bus(hub_pw)
              + [f"{hub_home}/.local/bin/talaria", "setup", "--as-hub", "--register",
                 f"{app}:{user}"])
    if rc != 0:
        return rc
    print("DONE", flush=True)
    return 0


def _fresh_image(ctx, st) -> bool:
    git = releases(ctx)
    reg = ctx.app.published(ctx, list(git))
    tag = pick_candidate(ctx.app, git, reg, None, set(), ctx.conf.min_release)
    if not tag:
        say("STOP", f"no {ctx.app.title} release image found")
        return False
    try:
        st["current"] = ctx.app.fetch(ctx, tag, git[tag])
    except (rehearse.Transient, rehearse.Permanent, images.RevisionMismatch) as e:
        say("STOP", str(e))
        return False
    state.save(ctx.paths, st)
    ctx.conf.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    say("OK", f"{ctx.app.title} {tag} pulled and verified")
    return True


def _telegram_ready(ctx, api=None) -> int | None:
    """The bot token (stored by the person) and the pairing. None when both are there."""
    p = ctx.paths
    if not ctx.conf.telegram_token:
        say("ACTION REQUIRED",
            "create a Telegram bot: open @BotFather, send /newbot, copy the token. Then, in "
            "your own terminal (not through an agent), run:\n"
            f"  sudo -u {getpass.getuser()} -H {p.bin_link} set-token")
        return 10
    if not ctx.conf.telegram_user_id:
        api = api or telegram.TelegramAPI(ctx.conf.telegram_api, ctx.conf.telegram_token)
        code = telegram.new_code()
        who = telegram.pair(ctx, api, code, announce=lambda: say(
            "ACTION REQUIRED", f"in a private chat with your bot, send within 15 minutes:\n"
                               f"  /pair {code}"))
        if not who:
            say("STOP", "no /pair message arrived; run setup again for a new code")
            return 10
        write_env_value(p.env_file, "TALARIA_TELEGRAM_USER_ID", str(who["id"]))
        ctx.conf.telegram_user_id = who["id"]
        say("OK", f"paired with {who.get('first_name', '')} (@{who.get('username', '-')})")
    return None


def hub_phase(ctx, args, api=None) -> int:
    """The hub account's own setup (spec §7.2, §7.3): hub.conf, bot token and pairing,
    the bot and timer units. `--register app:user` adds an app."""
    p = ctx.paths
    ensure_dir(p.conf_dir)
    ensure_dir(p.state_dir)
    if not p.hub_conf.exists():
        p.hub_conf.write_text(HUB_CONF_HEAD)
    if getattr(args, "import_telegram", False):
        return import_telegram(ctx, sys.stdin)
    added = False
    try:
        if args.register:
            app, _, user = args.register.partition(":")
            added = register_app(p, app, user)
        ctx.conf = load_hub_conf(p)
    except ValueError as e:
        say("STOP", str(e))
        return 1
    rc = _telegram_ready(ctx, api)
    if rc is not None:
        return rc
    changed = units.install_hub_units(ctx)
    ctx.sh.run(["systemctl", "--user", "enable", "--now", "talaria-check.timer",
                "talaria-telegram.service"])
    if changed or added:      # a new app or new units: the bot must see them
        ctx.sh.run(["systemctl", "--user", "restart", "talaria-telegram.service"])
    names = ", ".join(a for a, _ in ctx.conf.apps) or "none yet"
    say("OK", f"Talaria hub ready; apps: {names}")
    return 0


def service_phase(ctx, args) -> int:
    p = ctx.paths
    requested = getattr(args, "app", None)
    if requested and p.conf_file.exists() and requested != ctx.conf.app:
        say("STOP", f"{p.conf_file} already selects app = {ctx.conf.app}; --app {requested} "
                    "contradicts it; omit --app to keep the existing app, or edit "
                    "talaria.conf by hand")
        return 1
    if ctx.conf.host_loopback and which("slirp4netns") is None:
        say("STOP", "host_loopback = true needs slirp4netns; install the slirp4netns package "
                    "(apt install slirp4netns) and run setup again")
        return 1
    ensure_dir(p.conf_dir)
    ensure_dir(p.state_dir)
    if not p.conf_file.exists():
        p.conf_file.write_text(ctx.app.initial_conf(ctx))
    if "check.time" in parse_kv(p.conf_file.read_text()):
        say("NOTE", "check.time in talaria.conf is not used any more; set it in the hub's "
                    "hub.conf")
    if "tailscale" in ctx.conf.dashboard_bind.split() and not ctx.conf.tailscale_ip:
        ip = ctx.sh.run(["tailscale", "ip", "-4"]).stdout.split()[0]
        try:
            check_bind(ctx.conf.dashboard_bind, p.conf_file, ip)
        except ValueError as e:
            say("STOP", str(e))
            return 1
        with open(p.conf_file, "a") as f:
            f.write(f"tailscale_ip = {ip}\n")
        ctx.conf.tailscale_ip = ip
    st = state.load(p)
    managed = adopt.is_managed(ctx) and st.get("current") is not None
    found = plan = None
    if not managed:
        if args.adopt and not ctx.app.can_adopt:
            say("STOP", f"adopting is not supported for {ctx.app.title}")
            return 1
        cands = adopt.detect(ctx) if ctx.app.can_adopt else []
        if args.adopt:
            cands = [f for f in cands if f.unit == args.adopt]
            if not cands:
                say("STOP", f"no {ctx.app.title} install with unit {args.adopt}")
                return 1
        stopped = adopt.stopped_quadlets(ctx, cands) if ctx.app.can_adopt else []
        if stopped and not args.adopt:
            q = stopped[0]
            say("STOP", f"found a {ctx.app.title} Quadlet that is not running: {q}; start it "
                        f"(systemctl --user start {q.stem}.service) and run setup again")
            return 1
        if not cands and p.quadlet.exists():
            say("STOP", f"{p.quadlet} exists but was not written by Talaria; start that "
                        f"{ctx.app.title} and run setup again to adopt it, or move the file away")
            return 1
        if len(cands) > 1:
            for f in cands:
                say("FOUND", f"{ctx.app.title} unit {f.unit} (container {f.name})")
            say("STOP", f"several {ctx.app.title} installs; choose one with --adopt UNIT")
            return 1
        if cands:
            found = cands[0]
            plan = adopt.plan(ctx, found)
            adopt.print_plan(plan, ctx.paths.hermes_env)
            if plan.problems:
                for x in plan.problems:
                    say("STOP", x)
                return 1
            if not args.adopt:
                say("ACTION REQUIRED", "adopting stops and restarts the running agent. Review "
                    f"the diff above, then run: talaria setup --adopt {found.unit}")
                return 0 if args.plan else 10
        else:
            say("OK", f"no existing {ctx.app.title} found: fresh install")
    if args.plan:
        say("PLAN", f"{ctx.app.prepare_summary}, install units, "
                    f"start {ctx.app.title}, verify")
        return 0

    try:
        prepared = ctx.app.prepare(ctx)
    except ValueError as e:
        say("STOP", str(e))
        return 1
    for line in prepared:
        say("OK", line)

    if ctx.conf.dashboard_bind.split() == ["loopback"] and which("tailscale"):
        say("OK", "Tailscale found: optionally set dashboard.bind = tailscale in talaria.conf "
                  "and run setup again to reach the dashboard over your tailnet")

    try:
        with lock.op_lock(p):
            if not managed:
                if found:
                    if adopt.apply(ctx, found, plan) != 0:
                        return 1
                    ctx.conf = load_conf(p)
                elif not renamable(ctx.conf.data_dir):
                    say("STOP", NOT_RENAMABLE.format(ctx.conf.data_dir))
                    return 1
                elif not _fresh_image(ctx, st):
                    return 1
            changed = units.install_units(ctx)
            images.retag(ctx, "current", state.load(p)["current"])
            if changed or not service.is_active(ctx):
                service.stop(ctx)
                service.start(ctx)
                reason = service.post_start_check(ctx)
                if reason:
                    say("STOP", f"{ctx.app.title} did not come up: {reason}")
                    if found:
                        print(adopt.manual_steps(ctx, plan))
                    return 1
    except lock.Busy:
        say("STOP", "a Talaria operation is running; run setup again in a minute")
        return 1
    say("OK", ctx.app.ready_text(ctx))
    return 0


def set_token(ctx) -> int:
    token = getpass.getpass("Telegram bot token: ") if sys.stdin.isatty() \
        else sys.stdin.readline().strip()
    if not TOKEN_RE.match(token):
        print("That does not look like a Telegram bot token.", file=sys.stderr)
        return 1
    try:
        me = TelegramAPI(ctx.conf.telegram_api, token).call("getMe")
    except ApiError as e:
        print(f"Telegram did not accept the token ({e}).", file=sys.stderr)
        return 1
    write_env_value(ctx.paths.env_file, "TALARIA_TELEGRAM_TOKEN", token)
    print(f"OK: bot @{me.get('username')} saved. Now run talaria setup again.")
    return 0


def setup(args) -> int:
    from talaria.ctx import make_ctx, make_hub_ctx
    from talaria.shell import Shell
    requested = getattr(args, "app", None)
    if requested is not None and requested not in apps.NAMES:
        say("STOP", f"unknown app: {requested!r}; choose one of {', '.join(apps.NAMES)}")
        return 1
    if getattr(args, "as_hub", False):
        return hub_phase(make_hub_ctx(), args)
    if args.as_service:
        return service_phase(make_ctx(app=requested), args)
    os.chdir("/")  # commands run as the service user, which may not enter the caller's cwd
    # The app to install, in order: --app; else --user, if that names a known app;
    # else hermes. Only the first two are forwarded to the service phase with --app --
    # a bare hermes default must not override an existing conf's app on a later run.
    if requested is not None:
        app, explicit = requested, True
    elif args.user in apps.NAMES:
        app, explicit = args.user, True
    else:
        app, explicit = "hermes", False
    return operator_phase(Shell(), args, app=app, explicit_app=explicit)

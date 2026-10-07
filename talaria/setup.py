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
from talaria.conf import check_bind, load_conf, write_env_value
from talaria.disk import NOT_RENAMABLE, renamable
from talaria.notify import ApiError, TelegramAPI
from talaria.state import ensure_dir
from talaria.tags import pick_candidate, releases

REPO = Path(__file__).resolve().parent.parent
TOKEN_RE = re.compile(r"^\d{3,}:[A-Za-z0-9_-]{30,}$")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")   # goes into a root shell block
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


def root_block(user: str, operator: str, create: bool) -> str:
    """One command the person pastes whole into their own terminal; sudo asks for the
    password. The quoted heredoc delimiter keeps their shell from expanding anything."""
    lines = []
    if create:
        lines += [f"id {user} >/dev/null 2>&1 || useradd --create-home --shell /bin/bash {user}",
                  f"grep -q '^{user}:' /etc/subuid || echo 'WARNING: {user} has no subuid range; see README'"]
    lines += [f"loginctl enable-linger {user}",
              "tmp=$(mktemp)",
              f"echo '{operator} ALL=({user}) NOPASSWD: ALL' > \"$tmp\"",
              "visudo -cf \"$tmp\"",   # validate before it can break sudo
              f"install -m 440 -o root -g root \"$tmp\" /etc/sudoers.d/talaria-{user}",
              "rm -f \"$tmp\"",
              "echo 'Talaria: root step done'"]
    return "\n".join(["sudo bash -euo pipefail <<'TALARIA'", *lines, "TALARIA"])


def install_url(url: str) -> str | None:
    """The service user has no SSH key: turn a GitHub SSH remote into https. None if the
    remote needs SSH otherwise."""
    m = GITHUB_SSH.match(url)
    if m:
        return f"https://github.com/{m[1]}"
    if re.match(r"^(ssh://|[\w.-]+@)", url):
        return None
    return re.sub(r"^(https?://)[^/@]*@", r"\1", url)   # never copy credentials along


def operator_phase(sh, args, *, getpwnam=pwd.getpwnam, operator=None, call=subprocess.call,
                   linger_dir=Path("/var/lib/systemd/linger"), app="hermes",
                   explicit_app=True) -> int:
    if app not in apps.NAMES:
        say("STOP", f"unknown app: {app!r}; choose one of {', '.join(apps.NAMES)}")
        return 1
    A = apps.get(app)
    operator = operator or getpass.getuser()
    user = args.user or app
    for name in (user, operator):
        if not NAME_RE.match(name):
            say("STOP", f"not a valid account name: {name!r}")
            return 1
    if prerequisites(sh):
        return 10
    if selinux_enforcing(sh):
        say("STOP", "SELinux is enforcing; Talaria v1 does not support that")
        return 1
    try:
        pw = getpwnam(user)
    except KeyError:
        if args.plan:
            say("PLAN", f"create the account {user}: setup prints a block to run as root")
            say("PLAN", f"then run setup again with --user {user} to install Talaria for it")
            return 0
        say("ACTION REQUIRED", f"paste this into your terminal (sudo asks for your password), "
            f"then run setup again with --user {user}:\n" + root_block(user, operator, create=True))
        return 10
    home, install = pw.pw_dir, f"{pw.pw_dir}/.local/share/talaria"
    # sudo may keep the caller's XDG_* dirs (e.g. on CI runners); podman and git must
    # use the service user's own
    sudo = ["sudo", "-n", "-u", user, "-H", "env", "-u", "XDG_CONFIG_HOME", "-u",
            "XDG_DATA_HOME", "-u", "XDG_STATE_HOME", "-u", "XDG_CACHE_HOME", f"HOME={home}"]
    sudo_ok = sh.run(["sudo", "-n", "-u", user, "true"], check=False).returncode == 0
    installed = sudo_ok and sh.run(["sudo", "-n", "-u", user, "test", "-e", install],
                                   check=False).returncode == 0
    if not args.user and not installed:
        say("FOUND", f"account {user} exists but Talaria is not installed for it")
        say("STOP", f"confirm with the person, then re-run with --user {user}")
        return 1
    if not sudo_ok or not (Path(linger_dir) / user).exists():
        say("ACTION REQUIRED", f"paste this into your terminal (sudo asks for your password), "
            f"then run setup again:\n"
            + root_block(user, operator, create=False))
        return 10
    tag = sh.run(["git", "-C", str(REPO), "describe", "--tags", "--exact-match"],
                 check=False)
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
    if args.plan:
        say("PLAN", f"install Talaria {ref} for {user} from {url}")
        say("PLAN", f"then: detect {A.title} (fresh or adopt), {A.prepare_summary}, Telegram bot "
                    f"token and pairing, units, start {A.title}, verify")
        return 0
    if not installed:
        sh.run(sudo + ["git", "clone", "-q", url, install], timeout=600)
    sh.run(sudo + ["git", "-C", install, "fetch", "-q", "--tags", "origin"], timeout=600)
    sh.run(sudo + ["git", "-C", install, "checkout", "-q", ref])
    sh.run(sudo + ["mkdir", "-p", f"{home}/.local/bin"])
    sh.run(sudo + ["ln", "-sfn", f"{install}/bin/talaria", f"{home}/.local/bin/talaria"])
    say("OK", f"Talaria {ref} installed for {user}")
    rest = (["--app", app] if explicit_app else []) + \
        (["--adopt", args.adopt] if args.adopt else [])
    return call(sudo + [f"XDG_RUNTIME_DIR=/run/user/{pw.pw_uid}",
                        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{pw.pw_uid}/bus",
                        f"{home}/.local/bin/talaria", "setup", "--as-service", *rest])


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


def service_phase(ctx, args, api=None) -> int:
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
        say("PLAN", f"{ctx.app.prepare_summary}, Telegram token and pairing, install units, "
                    f"start {ctx.app.title}, verify")
        return 0

    try:
        prepared = ctx.app.prepare(ctx)
    except ValueError as e:
        say("STOP", str(e))
        return 1
    for line in prepared:
        say("OK", line)

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
            ctx.sh.run(["systemctl", "--user", "enable", "--now", "talaria-check.timer",
                        "talaria-telegram.service"])
            ctx.sh.run(["systemctl", "--user", "restart", "talaria-telegram.service"])
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
    print("DONE", flush=True)
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
    from talaria.ctx import make_ctx
    from talaria.shell import Shell
    requested = getattr(args, "app", None)
    if requested is not None and requested not in apps.NAMES:
        say("STOP", f"unknown app: {requested!r}; choose one of {', '.join(apps.NAMES)}")
        return 1
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

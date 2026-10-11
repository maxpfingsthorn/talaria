#!/usr/bin/env python3
"""Add (or undo) a Tailscale serve redirect from <host>:443/admin to the tailnet-only admin port.

Run as root on the host, as `python3 -I`, from a clone checked out at the release tag. Usage:
  tailscale-admin-redirect.py --host <host>.<tailnet>.ts.net [--admin-port 10000] [--dry-run]
  tailscale-admin-redirect.py --undo <saved.json>
"""
import argparse
import contextlib
import copy
import datetime
import json
import os
import re
import subprocess
import sys

MIN_VERSION = (1, 92)


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def ts(*args, stdin=None):
    try:
        return subprocess.run(["tailscale", *args], input=stdin, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        die(f"tailscale {' '.join(args[:2])} failed: {e}")


def status():
    r = ts("serve", "status", "--json")
    if r.returncode != 0:
        die(f"tailscale serve status failed: {r.stderr.strip()}")
    try:
        cfg = json.loads(r.stdout)
    except ValueError:
        die("tailscale serve status did not print JSON")
    if not isinstance(cfg, dict):
        die("tailscale serve status did not print a JSON object")
    return cfg


def post(cfg):
    r = ts("debug", "localapi", "POST", "/localapi/v0/serve-config", "-", stdin=json.dumps(cfg))
    if r.returncode != 0:
        die(f"applying the serve config failed: {r.stderr.strip()}")


def check_version():
    r = ts("version")
    first = (r.stdout.splitlines() or [""])[0].strip()
    m = re.match(r"^(\d+)\.(\d+)", first)
    if r.returncode != 0 or not m or (int(m[1]), int(m[2])) < MIN_VERSION:
        die("needs Tailscale 1.92 or newer")


def handler_lists(cfg):
    web = cfg.get("Web")
    if not isinstance(web, dict):
        return {}
    return {k: sorted(v["Handlers"]) for k, v in web.items()
            if isinstance(v, dict) and isinstance(v.get("Handlers"), dict)}


def show(label, cfg):
    lists = handler_lists(cfg)
    for k in sorted(lists):
        print(f"  {label} {k}: {', '.join(lists[k]) or '(none)'}")
    if not lists:
        print(f"  {label}: no web handlers")


def looks_like_serve_config(cfg):
    return (isinstance(cfg, dict) and ("TCP" in cfg or "Web" in cfg)
            and all(isinstance(v, (dict, bool, str)) for v in cfg.values()))


def changed():
    die("the serve config changed while this ran; run it again")


def undo(path):
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        die(f"cannot read {path}: {e}")
    if not looks_like_serve_config(cfg):
        die(f"{path} does not look like a Tailscale serve config")
    check_version()
    current = status()
    show("current ", current)
    show("restored", cfg)
    if status() != current:
        changed()
    post(cfg)
    print("restored")


def add(host, port, dry):
    check_version()
    cfg = status()
    original = copy.deepcopy(cfg)
    key = f"{host}:443"
    web = cfg.get("Web")
    handlers = web.get(key, {}).get("Handlers") if isinstance(web, dict) and isinstance(web.get(key), dict) else None
    if not isinstance(handlers, dict) or not handlers:
        die(f"443 on {host} serves no paths; set up the funnel paths first (README)")
    if "/admin" in handlers:
        die("/admin is already configured on 443")

    print("443 before:", ", ".join(sorted(handlers)))
    handlers["/admin"] = {"Redirect": f"302:https://{host}:{port}${{REQUEST_URI}}"}
    print("443 after: ", ", ".join(sorted(handlers)))
    if dry:
        print("dry run: nothing applied")
        return

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    backup = f"tailscale-serve.before-admin-redirect.{stamp}.json"
    try:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as e:
        die(f"cannot write backup {backup}: {e}")
    with os.fdopen(fd, "w") as f:
        json.dump(original, f, indent=2)
    print(f"saved current config: {os.path.abspath(backup)}")
    try:
        if status() != original:
            changed()
        post(cfg)
    except SystemExit:
        try:
            untouched = status() == original
        except SystemExit:
            untouched = False
        if untouched:
            with contextlib.suppress(FileNotFoundError):
                os.remove(backup)
            print(f"removed the backup {os.path.abspath(backup)}; nothing was applied", file=sys.stderr)
        else:
            print(f"kept the backup {os.path.abspath(backup)}: the config may have changed; "
                  f"undo with --undo if needed", file=sys.stderr)
        raise
    after = status()
    if "/admin" not in (after.get("Web", {}).get(key, {}).get("Handlers") or {}):
        die("applied, but /admin is not present afterwards")
    print(f"done; undo with: python3 -I {os.path.abspath(sys.argv[0])} --undo {os.path.abspath(backup)}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host")
    p.add_argument("--admin-port", type=int, default=10000)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--undo", metavar="FILE")
    a = p.parse_args()
    if a.undo:
        return undo(a.undo)
    if not a.host or not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", a.host):
        p.error("--host <host>.<tailnet>.ts.net is required")
    if not 1 <= a.admin_port <= 65535 or a.admin_port == 443:
        p.error("--admin-port must be 1-65535 and not 443")
    add(a.host, a.admin_port, a.dry_run)


if __name__ == "__main__":
    main()

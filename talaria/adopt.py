from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from talaria import images, state, units
from talaria.backup import _sha256
from talaria.conf import write_env_value
from talaria.disk import NOT_RENAMABLE, dir_size, renamable
from talaria.hermes import config_version
from talaria.state import ensure_dir, write_json_atomic
from talaria.tags import is_release, key

MANAGED = {"HERMES_DASHBOARD", "HERMES_DASHBOARD_INSECURE", "HERMES_DASHBOARD_BASIC_AUTH_USERNAME",
           "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD", "HERMES_UID", "HERMES_GID", "HERMES_HOME",
           "HERMES_SKIP_CONFIG_MIGRATION"}


_ASSIGN = re.compile(r"""\b([A-Za-z_][A-Za-z0-9_]*=)("[^"]*"|'[^']*'|[^\s"']+)""")


def mask_env(quadlet: str) -> str:
    """Hide the values in Environment= and PodmanArgs= lines: they often hold API keys,
    and the plan is printed to a terminal (and a coding agent's transcript)."""
    out = []
    for line in quadlet.splitlines(True):
        key = line.split("=", 1)[0].strip()
        if key in ("Environment", "PodmanArgs") and "=" in line:
            head, rest = line.split("=", 1)
            line = head + "=" + _ASSIGN.sub(r"\1***", rest)
        out.append(line)
    return "".join(out)


@dataclass
class Found:
    unit: str
    container: str
    name: str
    image_id: str
    mounts: list
    env: dict
    quadlet: Path | None


@dataclass
class Plan:
    found: Found
    image: dict | None
    data_dir: Path | None
    kept: dict
    dropped: list
    problems: list
    diff: str


def _env(lst) -> dict:
    return dict(e.split("=", 1) for e in lst or [] if "=" in e)


MANAGED_HEADER = "# Managed by Talaria."


def is_managed(ctx) -> bool:
    """True only for a hermes.container that Talaria itself wrote."""
    try:
        return ctx.paths.quadlet.read_text().startswith(MANAGED_HEADER)
    except FileNotFoundError:
        return False


def stopped_quadlets(ctx, found: list) -> list:
    """Hermes-looking Quadlet files that no detected container belongs to."""
    seen = {f.quadlet for f in found}
    out = []
    for q in sorted(ctx.paths.quadlet_dir.glob("*.container")) if ctx.paths.quadlet_dir.is_dir() else []:
        if q in seen or (q == ctx.paths.quadlet and is_managed(ctx)):
            continue
        text = q.read_text(errors="replace")
        if "/opt/data" in text or "HERMES_HOME" in text:
            out.append(q)
    return out


def detect(ctx) -> list[Found]:
    ids = [c["Id"] for c in json.loads(
        ctx.sh.run(["podman", "ps", "-a", "--format", "json"]).stdout or "[]")]
    if not ids:
        return []
    out = []
    for c in json.loads(ctx.sh.run(["podman", "container", "inspect", *ids]).stdout):
        env = _env(c["Config"].get("Env"))
        if "HERMES_HOME" not in env:
            continue
        labels = c["Config"].get("Labels") or {}
        unit = labels.get("PODMAN_SYSTEMD_UNIT") or f"container-{c['Name']}.service"
        if unit == "hermes.service" and is_managed(ctx):
            continue
        q = ctx.paths.quadlet_dir / (unit[:-len(".service")] + ".container")
        out.append(Found(unit=unit, container=c["Id"], name=c["Name"], image_id=c["Image"],
                         mounts=c.get("Mounts") or [], env=env,
                         quadlet=q if q.exists() else None))
    return out


def plan(ctx, f: Found) -> Plan:
    problems = []
    m = f.mounts
    data_dir = None
    if len(m) != 1 or m[0].get("Destination") != "/opt/data" or m[0].get("Type") != "bind":
        problems.append("only a single bind mount at /opt/data is supported; found: "
                        + (", ".join(f"{x.get('Source')}→{x.get('Destination')}" for x in m)
                           or "none"))
    else:
        data_dir = Path(m[0]["Source"])
        if not renamable(data_dir):
            problems.append(NOT_RENAMABLE.format(data_dir))
    image = images.local_record(ctx, f.image_id)
    tag = image.get("tag")
    if not tag or not is_release(tag) or key(tag) < key(ctx.conf.min_release):
        problems.append(f"Hermes {tag or 'of unknown version'} is older than "
                        f"{ctx.conf.min_release}; update it by hand first")
    img_env = _env(json.loads(ctx.sh.run(["podman", "image", "inspect", f.image_id]).stdout)[0]
                   .get("Config", {}).get("Env"))
    kept, dropped = {}, []
    for k, v in sorted(f.env.items()):
        if img_env.get(k) == v:
            continue
        if k in MANAGED or not (k.startswith("HERMES_") or k == "TZ"):
            dropped.append(k)
        else:
            kept[k] = v
    diff = ""
    if data_dir:
        saved = ctx.conf.data_dir
        ctx.conf.data_dir = data_dir
        new = units.render_quadlet(ctx)
        ctx.conf.data_dir = saved
        old = mask_env(f.quadlet.read_text()) if f.quadlet \
            else "(container without a Quadlet file)\n"
        diff = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                            str(f.quadlet or f.unit), "hermes.container"))
    return Plan(f, image, data_dir, kept, dropped, problems, diff)


def print_plan(p: Plan, hermes_env="~/.config/talaria/hermes.env") -> None:
    print(f"FOUND: Hermes {(p.image or {}).get('tag')} in unit {p.found.unit}, "
          f"data at {p.data_dir}")
    if p.kept:
        print("  environment kept: " + ", ".join(p.kept))
    if p.dropped:
        print("  environment NOT carried over: " + ", ".join(p.dropped))
        print(f"    Hermes reads its API keys from {p.data_dir}/.env, which is kept. If Hermes "
              f"needs any of these variables, add them to {hermes_env} before continuing.")
    if p.diff:
        print(p.diff)


def manual_steps(ctx, p: Plan) -> str:
    bid = state.load(ctx.paths).get("adopt_backup") or "<adopt backup>"
    arc = ctx.paths.backups / f"{bid}.tar.gz"
    d, f = p.data_dir, p.found
    lines = ["Manual way back to the previous install:",
             "  systemctl --user stop hermes.service",
             f"  mv {d} {d}.talaria-failed && mkdir {d}",
             f"  podman unshare tar --numeric-owner -xzf {arc} -C {d}",
             f"  rm {ctx.paths.quadlet}"]
    if f.quadlet:
        lines += [f"  mv {f.quadlet}.talaria-orig {f.quadlet}",
                  "  systemctl --user daemon-reload",
                  f"  systemctl --user start {f.unit}"]
    else:
        u = ctx.paths.units_dir / f.unit
        lines += [f"  mv {u}.talaria-orig {u}   # if it exists",
                  "  systemctl --user daemon-reload",
                  f"  systemctl --user enable --now {f.unit}"]
    return "\n".join(lines)


def apply(ctx, f: Found, p: Plan) -> int:
    sh, data = ctx.sh, p.data_dir
    sh.run(["systemctl", "--user", "stop", f.unit], check=False, timeout=600)
    sh.run(["podman", "stop", f.container], check=False, timeout=600)
    ensure_dir(ctx.paths.backups)
    bid = f"{ctx.now():%Y%m%dT%H%M%SZ}-adopt"
    arc = ctx.paths.backups / f"{bid}.tar.gz"
    tmp = ctx.paths.backups / f".{bid}.tar.gz.tmp"
    sh.run(["podman", "unshare", "tar", "--numeric-owner", "-czf", str(tmp), "-C", str(data),
            "."], timeout=7200)
    os.replace(tmp, arc)
    write_json_atomic(ctx.paths.backups / f"{bid}.json", {
        "id": bid, "label": "adopt", "created": ctx.now().isoformat(), "image": p.image,
        "cfg_version": config_version(data), "sha256": _sha256(arc),
        "size": arc.stat().st_size, "data_size": dir_size(data)})
    if sh.run(["podman", "unshare", "find", str(data), "!", "-user", "0", "-print",
               "-quit"]).stdout.strip():
        sh.run(["podman", "unshare", "chown", "-R", "0:0", str(data)], timeout=3600)
    sh.run(["podman", "rm", "-f", f.container], check=False)
    if f.quadlet:
        os.rename(f.quadlet, f.quadlet.with_name(f.quadlet.name + ".talaria-orig"))
    else:
        sh.run(["systemctl", "--user", "disable", f.unit], check=False)
        unit_file = ctx.paths.units_dir / f.unit
        if unit_file.exists():
            os.rename(unit_file, unit_file.with_name(unit_file.name + ".talaria-orig"))
    write_env_value(ctx.paths.conf_file, "data_dir", str(data))
    for k, v in p.kept.items():
        write_env_value(ctx.paths.hermes_env, k, v)
    st = state.load(ctx.paths)
    st["current"], st["adopt_backup"] = p.image, bid
    state.save(ctx.paths, st)
    print(f"OK: adopted {f.unit}; backup {bid}")
    return 0

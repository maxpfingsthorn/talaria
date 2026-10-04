from __future__ import annotations

import json
import re
import secrets
import shutil
from pathlib import Path

from talaria import tags
from talaria.apps.base import App
from talaria.conf import parse_kv, write_env_value
from talaria.containers import HelperError, run_doctor, run_helper
from talaria.images import ImageMissing, pull_verify
from talaria.state import ensure_dir
from talaria.upstream import git_release_tags, registry_tags

_CFG = re.compile(r"^_config_version:\s*(\d+)\s*$", re.M)
EMPTY_DIFF = {"ok": True, "changed": [], "added": [], "removed": [], "error": None}
PROBLEM = re.compile(r"[✗✘✖⚠❌]|\b(error|fail(ed|ure)?|warning)\b", re.I)
MAX_KEYS = 10


def _doctor_changes(before: str, after: str) -> list[str]:
    """Only problem lines that are new ("+") or gone ("-"); passing checks are noise."""
    b, a = before.splitlines(), after.splitlines()
    out = [f"+ {l.strip()}" for l in a if PROBLEM.search(l) and l not in b]
    out += [f"- {l.strip()}" for l in b if PROBLEM.search(l) and l not in a]
    return out[:40]


def _keys(sign: str, items: list, word: str) -> list[str]:
    lines = [f"{sign} {k}" for k, *_ in items[:MAX_KEYS]]
    if len(items) > MAX_KEYS:
        lines.append(f"… and {len(items) - MAX_KEYS} more {word}")
    return lines


def _fmt_diff(d: dict) -> str:
    """Changed values in full; added and removed keys by name only."""
    lines = [f"~ {k}: {o} → {n}" for k, o, n in d.get("changed", [])]
    lines += _keys("+", d.get("added", []), "added")
    lines += _keys("-", d.get("removed", []), "removed")
    return "\n".join(lines)


def _diff_title(d: dict) -> str:
    counts = [f"{len(d.get(k, []))} {k}" for k in ("changed", "added", "removed") if d.get(k)]
    return "Config changes: " + ", ".join(counts)


class Hermes(App):
    name = "hermes"
    title = "Hermes"
    unit = "hermes.service"
    container = "hermes"
    quadlet_file = "hermes.container"
    env_file = "hermes.env"
    local_image = "localhost/hermes-agent"
    default_data_dir = "~/hermes-data"
    default_port = 9119
    container_port = 9119
    default_image = "docker.io/nousresearch/hermes-agent"
    default_repo = "https://github.com/NousResearch/hermes-agent"
    min_release = "v2026.6.5"
    backup_exclude = (".cache", ".npm", "home/.cache", "home/.npm", "backups")
    can_adopt = True

    def is_release(self, tag: str) -> bool:
        return tags.is_release(tag)

    def tag_key(self, tag: str) -> tuple:
        return tags.key(tag)

    def releases(self, ctx) -> dict:
        return git_release_tags(ctx.sh, ctx.conf.hermes_repo)

    def published(self, ctx, tags) -> set:
        return registry_tags(ctx.sh, ctx.conf.image, ctx.conf.registry_tls_verify)

    def image_refs(self, ctx) -> list[str]:
        return [ctx.conf.image]

    def fetch(self, ctx, tag: str, commit: str) -> dict:
        return pull_verify(ctx, tag, commit)

    def reacquire(self, ctx, rec: dict) -> None:
        if not rec.get("ref"):
            raise ImageMissing(f"local image {rec['id'][:19]} is gone and cannot be pulled again")
        tls = [] if ctx.conf.registry_tls_verify else ["--tls-verify=false"]
        ctx.sh.run(["podman", "pull", "-q", *tls, rec["ref"]], timeout=3600)

    def health(self, ctx) -> str | None:
        url = f"http://{ctx.conf.bind_ip}:{ctx.conf.dashboard_port}/api/status"
        code, body = ctx.http_get(url, 5.0)
        if code != 200:
            return f"/api/status answered {code or 'nothing'}"
        try:
            if json.loads(body).get("auth_required") is True:
                return None
        except ValueError:
            pass
        return "/api/status does not report auth_required: true"

    def data_version(self, data_dir) -> int | None:
        cfg = Path(data_dir) / "config.yaml"
        if cfg.is_symlink():
            return None
        try:
            found = _CFG.findall(cfg.read_text())
        except (FileNotFoundError, UnicodeDecodeError):
            return None
        return int(found[0]) if len(found) == 1 else None

    def rehearse(self, ctx, st, image, copy: Path, stage: Path) -> dict:
        from talaria.rehearse import Permanent
        try:
            cfg = copy / "config.yaml"
            has_cfg = cfg.is_file() and not cfg.is_symlink()   # never read through a symlink
            # equivalent mutant: has_cfg already excludes symlinks, so follow_symlinks never matters
            if has_cfg:
                shutil.copy2(cfg, stage / "config.orig.yaml", follow_symlinks=False)  # pragma: no mutate
            doc_before = run_doctor(ctx, st["current"], copy) if st.get("current") else ""
            mig = run_helper(ctx, image, "migrate.py", copy, stage)
            if not mig["ok"]:
                raise Permanent(f"config migration failed: {mig['error']}", mig["messages"])
            db = run_helper(ctx, image, "dbopen.py", copy, stage)
            if not db["ok"]:
                raise Permanent(f"state.db could not be opened: {db['error']}")
            doc_after = run_doctor(ctx, image, copy)
            diff = run_helper(ctx, image, "confdiff.py", copy, stage,
                              args=["/opt/talaria-out/config.orig.yaml", "/opt/data/config.yaml"]) \
                if has_cfg else EMPTY_DIFF
        except HelperError as e:
            raise Permanent(str(e)) from None
        return {"tag": image["tag"], "digest": image["digest"], "cfg_before": mig["before"],
                "cfg_after": mig["after"], "db": db, "messages": mig["messages"],
                "diff": diff, "doctor": _doctor_changes(doc_before, doc_after)}

    def report_lines(self, ctx, report: dict) -> tuple[list[str], list]:
        lines = [f"Config version: {report['cfg_before']} → {report['cfg_after']}"]
        db = report["db"]
        held = db["after"] is not None and db["schema_version"] and db["after"] < db["schema_version"]
        lines.append(f"state.db: {db['before']} → {db['after']}"
                     + (f" (held back; image supports {db['schema_version']})" if held else ""))
        blocks = []
        if report["messages"]:
            blocks.append((f"Migrations that ran ({len(report['messages'])})",
                           "\n".join(report["messages"])))
        if _fmt_diff(report["diff"]):
            blocks.append((_diff_title(report["diff"]), _fmt_diff(report["diff"])))
        if report["doctor"]:
            blocks.append((f"Doctor: new or fixed problems ({len(report['doctor'])})",
                           "\n".join(report["doctor"])))
        else:
            lines.append("Doctor: no new problems.")
        return lines, blocks

    def pending_extra(self, report: dict) -> dict:
        return {"cfg_after": report["cfg_after"]}

    def before_start(self, ctx, pending: dict) -> tuple[str | None, list]:
        ensure_dir(ctx.paths.staging)
        mig = run_helper(ctx, pending["image"], "migrate.py", ctx.conf.data_dir, ctx.paths.staging)
        if not mig["ok"]:
            return f"migration failed: {mig['error']}", mig["messages"]
        if mig["after"] != pending["cfg_after"]:
            return (f"config version {mig['after']}, expected {pending['cfg_after']} "
                    "from the rehearsal"), mig["messages"]
        return None, []

    def prepare(self, ctx) -> list[str]:
        env = parse_kv(ctx.paths.app_env.read_text()) if ctx.paths.app_env.exists() else {}
        if "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD" in env:
            return []
        write_env_value(ctx.paths.app_env, "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD",
                        secrets.token_urlsafe(24))
        return [f"dashboard password generated in {ctx.paths.app_env} (user admin)"]

    def ready_text(self, ctx) -> str:
        return (f"Hermes is running. Dashboard: http://{ctx.conf.bind_ip}:"
                f"{ctx.conf.dashboard_port} (user admin, password in {ctx.paths.app_env})")

    def initial_conf(self, ctx) -> str:
        return "# Talaria settings; see README.\ndata_dir = ~/hermes-data\n"


APP = Hermes()

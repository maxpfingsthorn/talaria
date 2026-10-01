"""Semantic diff of two Hermes config files. Secret-looking values are masked."""
import json
import os
import re
import sys

SECRET = re.compile(r"(key|token|secret|password|passwd|credential|auth|bearer)", re.I)
URL_CREDS = re.compile(r"(://)[^/@\s]+@")


def flatten(d, prefix=""):
    out = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


# All patterns run in linear time: config values can be large.
URL_PARAM = re.compile(r"([?&])([^=&#?\s]*)=([^&#\s]*)")
FLAG = re.compile(r"^(--?[\w-]+)(=.*)?$", re.S)
AUTH = re.compile(r"\b(bearer|basic|token)\s+\S+", re.I)
PREFIXED = re.compile(r"\b(sk-[\w-]{8,}|gh[pousr]_\w{20,}|github_pat_\w{20,}"
                      r"|xox[abprs]-[\w-]{10,}|AIza[\w-]{20,}|\d{6,12}:[\w-]{30,})")
LONG = re.compile(r"[\w-]{32,}")


def _param(m):
    return f"{m[1]}{m[2]}=***" if SECRET.search(m[2]) else m[0]


def _long(m):
    s = m[0]
    return "***" if any(c.isdigit() for c in s) and any(c.isalpha() for c in s) else s


def secret_flag(s) -> bool:
    m = FLAG.match(s) if isinstance(s, str) else None
    return bool(m and SECRET.search(m[1]))


def sanitize_str(s: str) -> str:
    if secret_flag(s) and "=" in s:
        return s.split("=", 1)[0] + "=***"
    s = URL_CREDS.sub(r"\1***@", s)
    s = URL_PARAM.sub(_param, s)
    s = AUTH.sub(lambda m: f"{m[1]} ***", s)
    s = PREFIXED.sub("***", s)
    return LONG.sub(_long, s)


def sanitize(value):
    """Mask secret-looking keys at any depth, the value after a secret-looking flag in a
    list, credentials and secret parameters in URLs, and token-shaped strings."""
    if isinstance(value, dict):
        return {k: "***" if SECRET.search(str(k)) else sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        out = []
        for i, v in enumerate(value):
            secret_arg = i > 0 and secret_flag(value[i - 1]) and "=" not in value[i - 1]
            out.append("***" if secret_arg else sanitize(v))
        return out
    if isinstance(value, str):
        return sanitize_str(value)
    return value


def mask(key, value):
    return "***" if any(SECRET.search(seg) for seg in key.split(".")) else sanitize(value)


def diff(old: dict, new: dict) -> dict:
    a, b = flatten(old), flatten(new)
    a.pop("_config_version", None)
    b.pop("_config_version", None)
    changed = [[k, mask(k, a[k]), mask(k, b[k])] for k in sorted(a.keys() & b.keys())
               if a[k] != b[k]]
    added = [[k, mask(k, b[k])] for k in sorted(b.keys() - a.keys())]
    removed = [[k, mask(k, a[k])] for k in sorted(a.keys() - b.keys())]
    return {"changed": changed, "added": added, "removed": removed}


def main(argv=None) -> None:
    import yaml
    old_path, new_path = (argv or sys.argv[1:])[:2]
    try:
        with open(old_path) as f:
            old = yaml.safe_load(f) or {}
        with open(new_path) as f:
            new = yaml.safe_load(f) or {}
        res = {"ok": True, "error": None, **diff(old, new)}
    except Exception as e:
        res = {"ok": False, "error": f"{type(e).__name__}: {e}",
               "changed": [], "added": [], "removed": []}
    with open(os.environ["TALARIA_RESULT"], "w") as f:
        json.dump(res, f, default=str)


if __name__ == "__main__":
    main()
    sys.exit(0)

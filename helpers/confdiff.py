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


def sanitize(value):
    """Mask secret-looking keys at any depth and credentials inside URLs."""
    if isinstance(value, dict):
        return {k: "***" if SECRET.search(str(k)) else sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        return URL_CREDS.sub(r"\1***@", value)
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

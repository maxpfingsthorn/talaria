from __future__ import annotations

from talaria import backup, images, marker


def apply(ctx, st: dict) -> None:
    protect = set()
    m = marker.read(ctx.paths)
    if m:
        protect.add(m["backup"])
    if st.get("last_deploy"):
        protect.add(st["last_deploy"]["backup"])
    if st.get("adopt_backup"):
        protect.add(st["adopt_backup"])
    backup.prune(ctx, protect)
    keep = [st.get("current"), st.get("previous"), (st.get("pending") or {}).get("image")]
    keep += [b.meta.get("image") for b in backup.list_backups(ctx)]
    images.prune(ctx, keep)

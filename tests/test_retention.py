import copy

from talaria import backup, images, marker, retention, state
from tests.fakes import make_test_ctx


def test_retention_protects_and_keeps_exactly(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path, backup_keep=1)
    ids = []
    for i in range(5):
        ids.append(backup.create(ctx, f"b{i}", {"id": f"img{i}"}).id)
        ctx.clock.sleep(1)
    st = copy.deepcopy(state.DEFAULT)
    st.update(current={"id": "cur"}, previous={"id": "prev"},
              pending={"tag": "t", "image": {"id": "pend"}},
              last_deploy={"tag": "x", "backup": ids[1]}, adopt_backup=ids[0])
    marker.write(ctx.paths, "deploy", ids[2], {"id": "m"}, ctx.now())
    kept_images = []
    monkeypatch.setattr(images, "prune", lambda c, keep: kept_images.append(keep))
    retention.apply(ctx, st)
    left = sorted(b.id for b in backup.list_backups(ctx))
    assert left == sorted([ids[4], ids[2], ids[1], ids[0]])
    assert kept_images == [[{"id": "cur"}, {"id": "prev"}, {"id": "pend"},
                            {"id": "img4"}, {"id": "img2"}, {"id": "img1"}, {"id": "img0"}]]


def test_retention_with_nothing_to_protect(tmp_path, monkeypatch):
    ctx = make_test_ctx(tmp_path, backup_keep=1)
    for i in range(2):
        backup.create(ctx, f"b{i}", None)
        ctx.clock.sleep(1)
    monkeypatch.setattr(images, "prune", lambda c, keep: None)
    retention.apply(ctx, copy.deepcopy(state.DEFAULT))
    assert len(backup.list_backups(ctx)) == 1

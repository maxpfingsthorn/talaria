from __future__ import annotations

from talaria import tags
from talaria.apps.base import App
from talaria.images import ImageMissing, pull_verify
from talaria.upstream import git_release_tags, registry_tags


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

    is_release = staticmethod(tags.is_release)
    tag_key = staticmethod(tags.key)

    def releases(self, ctx) -> dict:
        return git_release_tags(ctx.sh, ctx.conf.hermes_repo)

    def published(self, ctx, tags) -> set:
        return registry_tags(ctx.sh, ctx.conf.image, ctx.conf.registry_tls_verify)

    def fetch(self, ctx, tag: str, commit: str) -> dict:
        return pull_verify(ctx, tag, commit)

    def reacquire(self, ctx, rec: dict) -> None:
        if not rec.get("ref"):
            raise ImageMissing(f"local image {rec['id'][:19]} is gone and cannot be pulled again")
        tls = [] if ctx.conf.registry_tls_verify else ["--tls-verify=false"]
        ctx.sh.run(["podman", "pull", "-q", *tls, rec["ref"]], timeout=3600)


APP = Hermes()

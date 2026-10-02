from __future__ import annotations


class App:
    """What differs between the apps Talaria manages. One instance per app, stateless."""
    name = ""
    title = ""
    unit = ""
    container = ""
    quadlet_file = ""
    env_file = ""
    local_image = ""
    default_data_dir = ""
    default_port = 0
    container_port = 0
    default_image = ""
    default_repo = ""
    min_release = ""
    backup_exclude: tuple = ()

    @staticmethod
    def is_release(tag: str) -> bool:
        raise NotImplementedError

    @staticmethod
    def tag_key(tag: str) -> tuple:
        raise NotImplementedError

    def releases(self, ctx) -> dict:
        raise NotImplementedError

    def published(self, ctx, tags) -> set:
        raise NotImplementedError

    def fetch(self, ctx, tag: str, commit: str) -> dict:
        raise NotImplementedError

    def reacquire(self, ctx, rec: dict) -> None:
        raise NotImplementedError

    def health(self, ctx) -> str | None:
        raise NotImplementedError

    @staticmethod
    def data_version(data_dir) -> int | str | None:
        raise NotImplementedError

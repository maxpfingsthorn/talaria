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

from __future__ import annotations

from talaria.apps.base import App


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


APP = Hermes()

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
    can_adopt = False
    fetch_error = "fetch failed"            # core prefix when ctx.app.fetch() raises CommandError
    before_start_error = "before_start failed"   # core prefix when ctx.app.before_start() raises
    prepare_summary = "secrets"             # what ctx.app.prepare() generates, for PLAN texts

    def is_release(self, tag: str) -> bool:
        raise NotImplementedError

    def tag_key(self, tag: str) -> tuple:
        raise NotImplementedError

    def releases(self, ctx) -> dict:
        raise NotImplementedError

    def published(self, ctx, tags) -> set:
        raise NotImplementedError

    def image_refs(self, ctx) -> list[str]:
        """Reference filters for `podman images`/`prune`. Never an empty string:
        an unfiltered listing would reach every image the user has, including the
        pinned base image."""
        raise NotImplementedError

    def fetch(self, ctx, tag: str, commit: str) -> dict:
        raise NotImplementedError

    def reacquire(self, ctx, rec: dict) -> None:
        raise NotImplementedError

    def health(self, ctx) -> str | None:
        raise NotImplementedError

    def data_version(self, data_dir) -> int | str | None:
        raise NotImplementedError

    def rehearse(self, ctx, st: dict, image: dict, copy, stage) -> dict:
        """Run the app's checks on `copy` (a copy of the data dir); return the report.
        Raises talaria.rehearse.Permanent when the candidate must not be deployed."""
        raise NotImplementedError

    def report_lines(self, ctx, report: dict) -> tuple[list[str], list]:
        """Plain message lines and untrusted (title, body) blocks for the candidate message."""
        raise NotImplementedError

    def pending_extra(self, report: dict) -> dict:
        """App-specific fields stored next to tag/image/report in st["pending"]."""
        raise NotImplementedError

    def before_start(self, ctx, pending: dict) -> tuple[str | None, list]:
        """Runs in place while the deploy marker is set, before the app is started.
        Returns (failure reason or None, untrusted details)."""
        return None, []

    def after_start(self, ctx, pending: dict) -> str | None:
        """Extra check after service.post_start_check passes. Returns a failure reason or None."""
        return None

    def quadlet_vars(self, ctx) -> dict:
        """Template variables for this app's quadlet, beyond the shared ones."""
        return {}

    def prepare(self, ctx) -> list[str]:
        """Create any missing secrets (never overwrite). Returns OK-line texts to print."""
        raise NotImplementedError

    def ready_text(self, ctx) -> str:
        """The final OK line once the app is confirmed running."""
        raise NotImplementedError

    def initial_conf(self, ctx) -> str:
        """Contents written to a fresh talaria.conf."""
        raise NotImplementedError

# Building Hermes images with rootless BuildKit

Not used by Talaria v1. Kept for a future `build` mode (spec §15).

## Why BuildKit

From v2026.7.1 the upstream Dockerfile uses `COPY --link --chmod=a+rX,go-w`.
Symbolic `--chmod` is a BuildKit-only feature: buildah (podman 4.9 up to at least
5.8) fails with `Error parsing chmod a+rX,go-w`. BuildKit builds the Dockerfile
unmodified.

## Recipe

BuildKit runs as root *inside the service user's rootless podman*, so root there is
the service user on the host. `moby/buildkit:rootless` does not work in this setup:
it needs a nested 65536-ID range that a rootless container does not have.

Minimal capabilities (Docker's default set for `RUN` steps plus `SYS_ADMIN`):
`--cap-add=SYS_ADMIN,AUDIT_WRITE,MKNOD,NET_RAW`, default seccomp and AppArmor.

Cap the cache in `buildkitd.toml`:

```toml
[worker.oci]
  gc = true
  reservedSpace = "1GB"
  maxUsedSpace = "5GB"
  minFreeSpace = "5GB"
```

Build and load, as the service user (`<ctx>` is an extracted `git archive` of the
tag, readable by the service user; pin the BuildKit image by digest):

```bash
podman run --rm --cap-add=SYS_ADMIN,AUDIT_WRITE,MKNOD,NET_RAW \
  -v <ctx>:/ctx:ro -v buildkit-state:/var/lib/buildkit \
  -v <path>/buildkitd.toml:/etc/buildkit/buildkitd.toml:ro \
  --entrypoint buildctl-daemonless.sh docker.io/moby/buildkit@sha256:<digest> \
  build --frontend dockerfile.v0 --local context=/ctx --local dockerfile=/ctx \
  --output type=docker,name=localhost/hermes-agent:<tag>,dest=- | podman load
```

A cold build of v2026.8.3 took about 6 minutes and produced a 2.8 GB image, the
same size as the official one.

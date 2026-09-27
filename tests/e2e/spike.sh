#!/usr/bin/env bash
# Feasibility check for the e2e suite (spec §13.2). Run on a fresh ubuntu-24.04 runner.
set -euxo pipefail
cd /   # the spike user cannot enter the runner workspace
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0 || true
sudo useradd --create-home --shell /bin/bash spike
sudo loginctl enable-linger spike
uid=$(id -u spike)
for _ in $(seq 30); do sudo test -S /run/user/$uid/bus && break; sleep 1; done
as() { sudo -u spike -H env -i HOME=/home/spike PATH=/usr/local/bin:/usr/bin:/bin XDG_RUNTIME_DIR=/run/user/$uid \
       DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus "$@"; }
as systemctl --user is-system-running || true
as podman run --rm --userns=keep-id:uid=10000,gid=10000 --user 10000:10000 \
   docker.io/library/alpine:3.20 id
as systemd-run --user --collect --wait true
sudo systemctl restart "user@$uid.service"
sleep 3
as systemctl --user is-system-running || true
echo SPIKE-OK

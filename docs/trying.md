# Trying Talaria

Talaria currently runs daily on the maintainer's host. Reports from other installations
are welcome: which Linux distribution, Podman version and app you tried, where setup
stopped, and what happened during recovery. Use the issue forms for problems or
[Show and tell](https://github.com/maxpfingsthorn/talaria/discussions/categories/show-and-tell)
for an installation report. Remove tokens, login links and identifying host details.

## Exercise the workflow on a disposable runner

You can inspect the [CI runs](https://github.com/maxpfingsthorn/talaria/actions/workflows/ci.yml)
without installing Talaria. The `e2e` job creates real service accounts, runs rootless
Podman and systemd, and drives updates, failed deployments, rollbacks, restores and
self-updates through a fake Telegram API. Its apps and release servers are stand-ins;
the separate contract tests exercise current upstream app releases.

To run that integration suite yourself:

1. Fork the repository on GitHub and enable Actions in the fork.
2. Open **Actions → ci → Run workflow** and choose the branch you want to evaluate.
3. Inspect the `e2e` job and its pytest output for the individual scenarios and results.

No Telegram token or provider keys are needed. GitHub supplies the disposable Ubuntu
runner. The suite creates users and changes system settings, so do not run it on your
normal host.

## Try a real install

Start with a disposable Ubuntu 24.04 VM and a normal login account with sudo access.
Keep the VM's systemd running; an ordinary container is not a substitute. Use fresh app
data for this evaluation.

Install the prerequisites from your own terminal:

```bash
sudo apt update
sudo apt install git python3 podman uidmap slirp4netns dbus-user-session sudo tar gzip
```

Follow [Setup by hand](../README.md#setup-by-hand), selecting the published release. Start
with Hermes, the default app. Setup prints the account and sudo changes for you to run,
then prompts you to store your bot token and pair Telegram. The
[Hermes guide](apps/hermes.md) explains how to reach the dashboard over an SSH tunnel.

Once setup prints `DONE`:

1. Send `/status`; check the version, running state and dashboard address.
2. Send `/check hermes`; if no new upstream release exists, expect it to report current.
   An update offer requires a newer release than the installed app.
3. When an update is offered, read the rehearsal report before approving it. Check the
   deployment result and `/status` afterwards.
4. On this disposable install, send `/backups hermes`, then `/rollback hermes`. Read the
   backup age and replacement warning before confirming. Check that both version and
   data return together; the pre-rollback backup can undo the rollback with `/restore`.

The fault scenarios that do not depend on a new upstream release are exercised by the
integration suite above. Destroy the VM when finished, or follow the
[uninstall instructions](operations.md#uninstall).

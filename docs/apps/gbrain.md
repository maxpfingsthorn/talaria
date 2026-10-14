# gbrain

Talaria can manage [gbrain](https://github.com/garrytan/gbrain), an agent memory "brain"
with an MCP server, the way it manages Hermes and Clawvisor. That covers release
detection, a rehearsal on a copy, Telegram approval, backup, deploy, verify, and
rollback of image and data together. gbrain runs in its own service account under the
same bot:

```bash
bin/talaria setup --plan --app gbrain --user gbrain
bin/talaria setup --app gbrain --user gbrain
```

- **`dashboard.public_url` is required.** It is the https address MCP connectors use,
  for example `dashboard.public_url = https://<host>.<tailnet>.ts.net` (include the port
  only if it is not 443).
  It becomes gbrain's `--public-url`, which is its OAuth issuer. Setup stops without it.
- **Image.** gbrain publishes no container image. Talaria downloads the release's
  `gbrain-linux-x64` and checks its SHA-256 against the digest GitHub publishes for that
  asset. When `gh` is installed and logged in for the service account, Talaria also checks
  the build provenance (`gh attestation verify`), which is the only check beyond
  same-release integrity. Otherwise setup prints a `NOTE` and only
  the digest is checked. Talaria then builds a local image from a pinned distroless base.
- **Storage.** gbrain keeps its data in PGLite (embedded Postgres) in `~gbrain/gbrain-data`,
  mode 0700, because some PGLite files inside it are world-readable. Only one process can
  open the brain at a time. Rehearsal copies, backups and maintenance therefore stop the
  server for a moment. The rehearsal never starts the new server: it runs
  `gbrain doctor --json` (schema version) and recalls a marker page that setup wrote, on
  the copy, offline and without your keys.
- **Admin token.** Setup generates `GBRAIN_ADMIN_BOOTSTRAP_TOKEN` in
  `~gbrain/.config/talaria/gbrain.env` (mode 0600), and you log in to `/admin` with it, or send `/login gbrain`
  to the Telegram bot for a one-time login link.
  Treat it like a password: never paste it into a chat or an agent's context.
- **Provider keys** (embeddings, synthesis, the dream cycle) are yours to add, in your own
  terminal as the service user: put them in the app env file
  `~<user>/.config/talaria/gbrain.env`, for example `OPENROUTER_API_KEY=…` (or
  `OPENAI_API_KEY=…`, `ANTHROPIC_API_KEY=…`, any provider gbrain supports), then run
  `sudo -u <user> XDG_RUNTIME_DIR=/run/user/$(id -u <user>) systemctl --user restart gbrain.service`. gbrain picks up a changed key only after a
  restart. Talaria never asks for provider keys and never prints them.
- **Model tiers** (utility, reasoning, deep, subagent) are gbrain's own configuration
  (`gbrain config`), not Talaria's. gbrain's defaults call Anthropic directly, so with
  another provider set the tiers to that provider's model ids. **Also pin `chat_model` and
  `expansion_model`** in the brain's config file (`~gbrain/gbrain-data/.gbrain/config.json`,
  for example `"chat_model": "openrouter:<model>"`) and restart gbrain: automatic
  extraction and synthesis check only these file pins, not the tiers, and otherwise fall back
  to an Anthropic default. An MCP client's `whoami` shows `chat_llm` as `ok` once it works.
  To choose embedding models
  for an existing brain, stop gbrain first, because the command needs the brain to itself.
  Run these as the service user (from another account, with `sudo -u <user>` and the
  `XDG_RUNTIME_DIR` shown, which rootless podman and `systemctl --user` need):

  ```bash
  sudo -u <user> XDG_RUNTIME_DIR=/run/user/$(id -u <user>) systemctl --user stop gbrain.service
  sudo -u <user> XDG_RUNTIME_DIR=/run/user/$(id -u <user>) podman run --rm --read-only --userns=keep-id:uid=65532,gid=65532 \
    -v ~<user>/gbrain-data:/data:Z -e HOME=/data -e GBRAIN_HOME=/data \
    --env-file ~<user>/.config/talaria/gbrain.env localhost/gbrain:current \
    embeddings enable --embedding-model <provider:model>
  sudo -u <user> XDG_RUNTIME_DIR=/run/user/$(id -u <user>) systemctl --user start gbrain.service
  ```
- **Update cadence.** gbrain releases several times a day. By default
  (`check.days = mon thu`) the daily timer looks for a gbrain release twice a week.
  `/check gbrain` looks at any time.
- **Nightly maintenance.** Each night, from `maintenance.time` (default `01:30`, local
  time), Talaria stops gbrain, runs `gbrain dream` once in a one-off container, and starts
  gbrain again. The dream cycle has network access and your provider keys; without keys it
  runs only its file-based phases. Expect minutes of downtime. The run is skipped while
  another Talaria operation runs or a change is interrupted. Success is silent. A failure
  sends one message, and the next night retries. If gbrain is not running afterwards,
  the message says so and `/rollback CONFIRM` in the bot starts it again (nothing is
  restored); there is no retry until then. The hub's `talaria-maintain.timer` checks
  every 15 minutes whether a window is due. `maintenance.time =` (empty) turns
  maintenance off.
- Out of scope: Postgres, gbrain's autopilot daemon, adopting an existing gbrain install,
  and gbrain's own `self-upgrade` (Talaria turns its update checks off).

## Who reaches what

| Path | Who | Where |
|---|---|---|
| `/mcp`, `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource` (and `…/mcp`), `/authorize`, `/token`, `/register`, `/revoke` | cloud MCP connectors (claude.ai, ChatGPT) | public, port 443 |
| everything, including `/admin` and `/metrics` | you, and local agents on the host or tailnet | tailnet only, port 10000 (or the published address) |
| `/admin` (redirect only) | anyone; answers with a redirect to the tailnet-only admin port | public, port 443 |

Cloud connectors use OAuth: they register themselves (dynamic client registration) and
you approve each one in `/admin`. **With registration open, anyone who reaches
`/register` can create a client.** It stays *pending*, and nothing is granted until you
approve it. Approve only clients you just added yourself. Local agents (Hermes, Claude
Code, Codex) get scoped tokens that you mint in `/admin`, and use the tailnet port or the
published address.

A connector's consent link and the bot's login links point at the public address
(`https://<host>.<tailnet>.ts.net/admin/…`). With the redirect below, that address sends your
browser to the tailnet-only admin port, keeping the rest of the link; outside your tailnet the
target does not answer. gbrain keeps admin sessions in memory, so every restart (also the
nightly maintenance) logs you out: send `/login gbrain` for a new link.

## Public MCP endpoints with Tailscale Serve and Funnel

Talaria does not configure Tailscale. For any app with a public MCP endpoint, publish the
app on loopback (`dashboard.bind = loopback`, the default) or on its Tailscale address.
Then, as root, once (`<port>` is the app's `dashboard.port`, 3131 for gbrain):

```bash
# public: exactly the connector paths, on 443
for p in /mcp /.well-known/oauth-authorization-server /.well-known/oauth-protected-resource \
         /authorize /token /register /revoke; do
  tailscale funnel --bg --https=443 --set-path "$p" "http://127.0.0.1:<port>$p"
done
# tailnet only: the whole server, including its admin UI, on 10000
tailscale serve --bg --https=10000 http://127.0.0.1:<port>
# the admin redirect on 443 (Tailscale 1.92+; the serve CLI has no flag for it)
git clone --branch vX.Y.Z https://github.com/maxpfingsthorn/talaria /root/talaria-vX.Y.Z   # the release you run
python3 -I /root/talaria-vX.Y.Z/tools/tailscale-admin-redirect.py --host <host>.<tailnet>.ts.net
tailscale funnel status
```

Run it from your own clone at the release tag, not from an installed copy: those belong to
service accounts that can edit them, and `-I` keeps the current directory and the environment out of
Python's import path, so nothing a service account can write runs as root. The helper adds
only `/admin` on 443, as a redirect (`302:https://<host>.<tailnet>.ts.net:10000${REQUEST_URI}`); it
first saves the previous config as `tailscale-serve.before-admin-redirect.<UTC stamp>.json` in the
current directory and prints that path (it removes the file again if it aborts before applying anything). `--undo <file>` shows the current and the restored paths
per port, then restores it (only a file that looks like a serve config). `--dry-run` shows the
plan and writes nothing. It refuses when `/admin` already exists or 443 has no paths yet, and
aborts if the serve config changes while it runs, so do not change it in the meantime.

Funnel must be allowed for the node in your tailnet policy, and it only works on ports
443, 8443 and 10000. **Funnel applies per port: never funnel a port that also serves a
private app.** `tailscale funnel status` should list only the paths above (and `/admin` as a redirect) as public.
Use 443: claude.ai's connector check did not reach a funnel on 8443. If 443 already
serves a private app (for example Clawvisor via `tailscale serve`), move that app to another
tailnet-only port first (`tailscale serve --https=443 off`, then `--https=<other port>`, and
update its `dashboard.public_url`). Set `dashboard.public_url = https://<host>.<tailnet>.ts.net`
and run setup again.

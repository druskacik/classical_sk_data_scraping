# Deferred scraper deployment

The shared `CapRoverUpdater` compares the deployed image SHA with `master`,
invokes a private Captain Webhook for newer commits, persists successful
requests, and keeps failed checks retryable. The crawler factory and normal
scraper runner apply their own scheduling guards around this shared mechanism.

The normal scraper app checks `master` after its final scheduled analyzer has
returned. Failed checks are retried every five minutes in that post-pipeline
window. Once the check succeeds or finds no update, deployment checks remain
disabled until the next daily pipeline finishes.

## CapRover configuration

1. Keep the app's existing repository deployment configured for the `master`
   branch and its current Dockerfile build.
2. Remove the scraper app's Captain Webhook from GitHub's push webhooks. A push
   must not invoke CapRover directly.
3. Copy the scraper app's private Captain Webhook into the app environment as
   `SCRAPER_DEPLOY_WEBHOOK`.
4. Add a persistent-directory mapping for `/var/lib/classical-bot`. It stores
   the last requested commit so restarts do not request the same deployment
   repeatedly.
5. Deploy this revision manually once. CapRover supplies
   `CAPROVER_GIT_COMMIT_SHA` during repository builds; the Dockerfile exposes it
   to the running process for comparison with `master`.

Optional settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SCRAPER_REPOSITORY` | `https://github.com/druskacik/classical_bot.git` | Repository queried for `master` |
| `SCRAPER_DEPLOY_STATE_PATH` | `/var/lib/classical-bot/deployment-state.json` | Persistent updater state |

If the webhook or deployed commit SHA is unavailable, scraping continues and
automatic deployment is disabled with a log message.

# TXB CRM — VPS Deployment Guide (Coolify + Docker + external MariaDB)

Target architecture: one custom Docker image (Frappe v15 + this `crm` fork,
frontend pre-built), orchestrated as a multi-container compose stack on a VPS
managed by Coolify, with MariaDB running externally. Built images live in a
private GHCR registry; GitHub Actions builds them on push.

```
GitHub (Mygom-tech/txb-crm) ── push ──▶ GH Actions ── build ──▶ GHCR (private image)
                                                                    │
                          Coolify (VPS) ◀────────── pull ───────────┘
                          ├─ frontend   nginx :8080  ◀── Traefik/TLS (Coolify)
                          ├─ backend    gunicorn :8000
                          ├─ websocket  socketio :9000
                          ├─ queue-short / queue-long / scheduler
                          ├─ redis-cache / redis-queue
                          └─ volume: sites/          MariaDB (external) ◀── backend/workers
```

> **Conventions in this guide**
>
> - `<PLACEHOLDER>` values are yours to fill in.
> - Every phase ends with a verification step — do not continue past a failed one.
> - Commands prefixed `local$` run on your dev machine, `vps$` on the server.

---

## Phase 0 — Pre-flight (dev machine, ~30 min)

The production image builds this repo **from a clean clone**. Two things in the
current fork have never been exercised that way; verify them before building
any pipeline on top.

**0.1 Clean-clone production build test**

```bash
local$ cd /tmp && git clone https://github.com/Mygom-tech/txb-crm.git txb-crm-clean
local$ cd txb-crm-clean && git checkout develop
local$ yarn install            # runs postinstall → frontend yarn install
local$ yarn build              # vite production build + copy-html-entry
```

Expected: build completes, `crm/public/frontend/` gets populated.

Known risks (fix in the repo if either bites, then re-test):

- `frontend/package.json` declares `"@framework/ui": "link:../../frappe/ui"` — a
  dangling link in any clone without that path. If `yarn install --check-files`
  or the build fails on it, **delete the dead dependency** (nothing in
  `frontend/src` imports it).
- The `frappe-ui` git submodule is only used by the **dev** server (`isDev`
  gate in `frontend/vite.config.js`); production builds use npm
  `frappe-ui`. If the build unexpectedly requires the submodule, add
  `git submodule update --init && cd frappe-ui && yarn install` before the
  build step everywhere this guide runs `yarn build`.

**0.2 Decide names** (used throughout; write them down):

| Item                            | Example                                    |
| ------------------------------- | ------------------------------------------ |
| Production domain               | `crm.example.com`                          |
| Site name (bench site = domain) | `crm.example.com`                          |
| Image                           | `ghcr.io/mygom-tech/txb-crm:latest`        |
| Frappe branch                   | `version-15` (bench currently runs 15.116) |
| CRM branch to deploy            | `develop`                                  |

---

## Phase 1 — External MariaDB (~30 min)

Any managed MariaDB or a separate DB VPS works. Requirements Frappe is strict
about:

**1.1 Server version & settings** — MariaDB **10.6+** with:

```ini
[mysqld]
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci
character-set-client-handshake = FALSE
innodb_read_only_compressed = OFF   # 10.6 default breaks frappe; must be off
```

**1.2 Network** — allow inbound `3306`/your port **only from the VPS IP**
(firewall / security group). Do not expose it to the world; your current dev
DB (`195.201.127.51:3307` with plaintext creds in `site_config.json`) is the
cautionary tale here.

**1.3 Credentials** — you need a **root-level user** reachable from the VPS
_once_, at site-creation time (Frappe creates the site's database and its own
scoped DB user itself). After site creation the root user can be locked back
down.

**Verify:** from the VPS (Phase 2): `mariadb -h <DB_HOST> -P <DB_PORT> -u root -p -e "SELECT @@version, @@character_set_server;"` → 10.6+, `utf8mb4`.

---

## Phase 2 — VPS + Coolify (~45 min)

**2.1 VPS sizing** — 2 vCPU / 4 GB RAM / 40 GB disk is a comfortable floor for
one site (gunicorn + 2 workers + 2 redis + nginx + socketio). Ubuntu 22.04/24.04.

**2.2 Install Coolify** (their one-liner, as root):

```bash
vps$ curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash
```

Open the Coolify UI, finish onboarding, point your DNS `A` record
(`crm.example.com`) at the VPS. Coolify's Traefik will issue Let's Encrypt
certificates automatically once a resource claims the domain.

**2.3 GHCR access** — Coolify has no per-resource private-registry credential
UI; it relies on the host Docker daemon's credentials. One-time, on the VPS:

```bash
vps$ docker login ghcr.io -u <github-username> -p <PAT with read:packages only>
```

After that, compose pulls of the private image authenticate transparently.
The PAT lives in the server's `~/.docker/config.json` — keep it
`read:packages`-only and rotate it if the box is ever compromised.

---

## Phase 3 — Image build (manual, from the dev machine) (~30 min)

Uses [frappe_docker](https://github.com/frappe/frappe_docker)'s layered
custom-app image, built **locally** and pushed to private GHCR — no CI.
Everything lives in this repo under `deploy/`:

- `deploy/apps.json` — which apps go in the image (`${GH_TOKEN}` placeholder
  is substituted at build time, never committed filled-in).
- `deploy/build.sh` — clones/refreshes frappe_docker into `/tmp`, builds the
  layered image tagged `ghcr.io/mygom-tech/txb-crm:<short-sha>` (+ `:latest`),
  and pushes both.

**3.1 One-time setup:**

```bash
# Fine-grained PAT #1 (build): read-only Contents on Mygom-tech/txb-crm
# Classic PAT #2 (push):  write:packages  (+ org SSO authorization if enforced)
docker login ghcr.io -u <github-username>   # use PAT #2 as the password
```

**3.2 Every release:**

```bash
cd apps/crm && git checkout develop && git pull
GH_TOKEN=<PAT-1> ./deploy/build.sh          # prints the sha tag when done
```

Notes:

- apps.json is passed as a **BuildKit secret** (`--secret id=apps_json`) —
  frappe_docker's current Containerfiles do NOT read the old
  `APPS_JSON_BASE64` build arg (an unconsumed arg silently builds a
  frappe-only image; the script's **app-presence gate** now hard-fails
  before pushing if `apps/crm` is missing). Secrets never persist in image
  layers, and the build strips `.git` dirs, so the token cannot leak via
  the image (this is also why `bench version` shows apps as UNVERSIONED).
- Secret mounts are invisible to BuildKit's layer cache — without the
  `CACHE_BUST` build-arg the script passes, a rebuild can silently reuse a
  stale app-install layer. Never remove that arg.
- Tags are `<commit-sha>-<timestamp>`, unique per build **by design**:
  Docker never re-pulls a tag it has cached locally, so re-pushing the same
  tag means servers keep running the old bytes with no error anywhere.
  `:latest` is pushed for humans browsing GHCR — it must never appear in
  `FRAPPE_VERSION`.
- Pin `FRAPPE_BRANCH` (default `version-15` via the script) to whatever prod
  actually runs — see the production-cutover runbook's pre-conditions.
- frappe_docker moves occasionally — if `images/layered/Containerfile` is
  renamed upstream, check their `docs/custom-apps.md` for the current path.
- If manual builds ever become a bottleneck or get forgotten, the same build
  translates 1:1 into a ~40-line GitHub Actions workflow (build-push-action
  with the same build args, `GITHUB_TOKEN` instead of PAT #1).

**Verify:** `docker pull ghcr.io/mygom-tech/txb-crm:<sha>` works from the VPS
with the registry credentials configured in Phase 2.3, and the GHCR package
page shows **Private** visibility (see 3.3).

**3.3 Registry visibility:** the first push creates the GHCR package. Under a
GitHub **org**, its default visibility follows the org's package settings —
check _github.com/orgs/Mygom-tech/packages → txb-crm → Package settings_ and
confirm **Private** explicitly rather than trusting the default. Package
visibility is independent of the repo's; making the repo private does not
retroactively protect a public package.

---

## Phase 4 — Compose stack in Coolify (~1 h)

Create a Coolify **Docker Compose** resource with the file below
(`deploy/compose.coolify.yml` in this repo). It is frappe_docker's standard
topology minus MariaDB (external) and minus their proxy (Coolify's Traefik
does TLS; the `frontend` nginx already reverse-proxies both gunicorn and
socketio internally, so **only `frontend` needs a domain**).

```yaml
x-app: &app
  image: ghcr.io/mygom-tech/txb-crm:${FRAPPE_VERSION}
  restart: unless-stopped
  volumes:
    - sites:/home/frappe/frappe-bench/sites

services:
  configurator:
    <<: *app
    restart: "no"
    entrypoint: ["bash", "-c"]
    command:
      - >
        bench set-config -g db_host $$DB_HOST;
        bench set-config -gp db_port $$DB_PORT;
        bench set-config -g redis_cache "redis://redis-cache:6379";
        bench set-config -g redis_queue "redis://redis-queue:6379";
        bench set-config -g redis_socketio "redis://redis-queue:6379";
        bench set-config -gp socketio_port 9000;
    environment:
      DB_HOST: ${DB_HOST}
      DB_PORT: ${DB_PORT}

  backend:
    <<: *app
    depends_on:
      configurator:
        condition: service_completed_successfully

  frontend:
    <<: *app
    command: ["nginx-entrypoint.sh"]
    environment:
      BACKEND: backend:8000
      SOCKETIO: websocket:9000
      FRAPPE_SITE_NAME_HEADER: ${SITE_NAME}
      UPSTREAM_REAL_IP_ADDRESS: 127.0.0.1
      UPSTREAM_REAL_IP_HEADER: X-Forwarded-For
      UPSTREAM_REAL_IP_RECURSIVE: "off"
      PROXY_READ_TIMEOUT: 120
      CLIENT_MAX_BODY_SIZE: 50m
    ports:
      - "8080"
    depends_on:
      - backend
      - websocket

  websocket:
    <<: *app
    command: ["node", "/home/frappe/frappe-bench/apps/frappe/socketio.js"]
    depends_on:
      configurator:
        condition: service_completed_successfully

  queue-short:
    <<: *app
    command: ["bench", "worker", "--queue", "short,default"]
    depends_on:
      configurator:
        condition: service_completed_successfully

  queue-long:
    <<: *app
    command: ["bench", "worker", "--queue", "long,default,short"]
    depends_on:
      configurator:
        condition: service_completed_successfully

  scheduler:
    <<: *app
    command: ["bench", "schedule"]
    depends_on:
      configurator:
        condition: service_completed_successfully

  redis-cache:
    image: redis:7-alpine
    restart: unless-stopped

  redis-queue:
    image: redis:7-alpine
    restart: unless-stopped
    volumes:
      - redis-queue-data:/data

volumes:
  sites:
  redis-queue-data:
```

Coolify setup for this resource:

- **Env vars** (Coolify secrets): `DB_HOST`, `DB_PORT`, `SITE_NAME=crm.example.com`.
- **Domain**: attach `crm.example.com` to the `frontend` service, port `8080`.
- Frappe's own nginx proxies `/socket.io` to the websocket container — no
  extra Traefik labels needed. If realtime doesn't work later, this internal
  proxy is the first place to look, not Traefik.

**Verify:** stack deploys; `docker ps` on the VPS shows all services healthy
(the site itself 404s until Phase 5 — that's expected).

---

## Phase 5 — Create (or migrate) the site (~30 min)

One-time, from a shell in the `backend` container (Coolify → resource →
terminal, or `vps$ docker exec -it <backend> bash`):

**5.1 Fresh site:**

```bash
bench new-site ${SITE_NAME} \
  --no-mariadb-socket \
  --db-host <DB_HOST> --db-port <DB_PORT> \
  --db-root-username root --db-root-password '<DB_ROOT_PW>' \
  --admin-password '<STRONG_ADMIN_PW>' \
  --install-app crm
bench --site ${SITE_NAME} enable-scheduler
```

**5.2 Migrating the existing dev data instead** (the current DB on
`195.201.127.51`): on the dev bench run
`bench --site localhost backup --with-files`, copy the three artifacts
(sql.gz + 2 files-tars) into the container's `sites/` volume, then:

```bash
bench --site ${SITE_NAME} restore /path/to/db.sql.gz \
  --with-public-files ... --with-private-files ...
bench --site ${SITE_NAME} migrate
```

**5.3 Production site config — the anti-dev checklist.** Confirm the prod
site's `site_config.json` has **neither** `developer_mode` **nor**
`ignore_csrf` (both are enabled on the dev bench; in prod they are security
holes, full stop). Then **immediately save a copy of the site's
`encryption_key`** from `site_config.json` into your password manager —
without it, restored backups cannot decrypt stored credentials
(integrations, email passwords), and it exists nowhere else.

**Verify:** `https://crm.example.com/crm` loads over TLS, login works, a
kanban drag shows the confirm modal (yes, your feature is the smoke test),
and `bench --site ${SITE_NAME} doctor` reports scheduler enabled + workers
online.

**Beware the create-site guard:** the compose's `create-site` service skips
whenever the site _directory_ exists — including after a provision that
failed halfway (site created, app install failed). It will then skip
forever, silently. After any first deploy, prove the site is whole:
`bench --site ${SITE_NAME} list-apps` must list **both** `frappe` and
`crm`; if not, see Troubleshooting.

---

## Phase 6 — Deploy & operate

**Deploy flow** (every release): merge to `develop` → `deploy/build.sh` →
pin the printed tag as `FRAPPE_VERSION` in Coolify → _Redeploy_ → then:

```bash
docker exec <backend> bench --site all migrate
docker exec <backend> bench --site all clear-cache
docker exec <backend> bench --site all clear-website-cache
```

Wire all three as Coolify's **post-deployment command** so they're never
forgotten. Both cache clears matter, and they clear _different_ things:

- `clear-cache` drops the shared `assets_json` key — Frappe's in-Redis copy of
  `sites/assets/assets.json`, read by `frappe.get_assets_json()` and used to
  render every `<link>`/`<script>` tag on every page.
- `clear-website-cache` drops the rendered-page/route cache.

Asset bundle hashes change per build, but `redis-cache` has no image change on
a redeploy, so Coolify does not recreate that container and its keys survive.
Skip `clear-cache` and Frappe keeps emitting the _previous_ build's hashes
site-wide — including on `/login` — while nginx correctly 404s them (symptom:
CSS/JS 404s with `text/html` MIME errors on every page after an upgrade).

**Post-deploy verification checklist** — run after EVERY deploy; a deploy
is not done until all four pass:

```bash
# 1. Containers run the tag you just pinned (not a cached older build)
docker ps --format '{{.Names}}  {{.Image}}' | grep txb-crm
# 2. The running backend contains the app
docker exec <backend> ls apps                       # → crm frappe
# 3. The site responds and serves fresh assets.
#    Check EVERY bundle, not one: a stale manifest breaks all of them at once,
#    and a single passing bundle proves nothing.
SITE=https://<site>
curl -s "$SITE/api/method/ping"                     # → {"message":"pong"}
curl -s "$SITE/login" | grep -oE '/assets/[^"]+\.(css|js)' | sort -u \
  | while read -r u; do
      code=$(curl -s -o /dev/null -w '%{http_code}' "$SITE$u")
      [ "$code" = 200 ] || echo "STALE MANIFEST: $code $u"
    done                                             # → prints nothing
# 4. The app works: log in, open kanban, drag a card → confirm modal
```

Rollback = point the compose image tag at the previous `:sha` and redeploy
(migrations are forward-only — restore a DB backup if a migration must be
undone).

**Backups** (non-negotiable given the DB holds real CRM data):

```bash
docker exec <backend> bench --site all backup --with-files
```

Schedule daily (Coolify scheduled task or host cron), and ship the artifacts
**off the VPS** (restic/rclone to S3-compatible storage). A backup on the same
disk as the database is a diary, not a backup. Test a restore once before you
need it.

**Monitoring floor:** Coolify's built-in container health + uptime check on
`https://crm.example.com/api/method/ping`; disk-space alert on the VPS (sites
volume grows with file uploads); MariaDB storage/connections on the DB side.

---

## Troubleshooting — field-tested (every entry below actually happened)

| Symptom                                                                                                                           | Cause                                                                                                                                                                                                                                                                    | Fix                                                                                                                                                                                                                            |
| --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Image builds fine but contains only `frappe`, no `crm`                                                                            | apps.json not reaching the build: wrong build-arg name, or BuildKit cache reusing the app-install layer                                                                                                                                                                  | `deploy/build.sh` handles both (secret mount + CACHE_BUST); its app-presence gate blocks the push. If it fires: check `deploy/apps.json` renders valid JSON with the token                                                     |
| Redeployed but behavior unchanged; asset hashes in HTML don't match the new image                                                 | VPS kept a locally-cached image for a reused tag (`:latest` or a re-pushed sha)                                                                                                                                                                                          | Deploy only unique `<sha>-<timestamp>` tags; verify with checklist step 1                                                                                                                                                      |
| `bench new-site`/`install-app` "failed", later runs say "already exists, skipping"                                                | The create-site guard is directory-based: a half-failed provision leaves the site dir and is skipped forever after                                                                                                                                                       | `bench --site <site> list-apps`; if `crm` missing → `install-app crm` + `migrate`. If the DB is empty garbage → `bench drop-site` and let create-site re-run                                                                   |
| Lead/Deal create modal blank; console: `Cannot read properties of undefined (reading 'name')` in statuses.js                      | App installed but seed data missing (statuses etc.) — another half-provision artifact; the frontend assumes ≥1 status exists                                                                                                                                             | `bench --site <site> execute crm.install.after_install` (idempotent, `db.exists` guards) + `clear-cache`                                                                                                                       |
| CSS/JS 404 with `text/html` MIME errors, on **every** page including `/login`                                                     | Stale shared `assets_json` key in `redis-cache` (that container isn't recreated on redeploy), so Frappe renders the previous build's bundle hashes. Confirm: `curl -s https://<site>/assets/assets.json` names different hashes than the page's `<link>`/`<script>` tags | `bench --site all clear-cache` (now in the post-deploy command), then re-run checklist step 3. Do **not** run `bench build` in a running container — that mints fresh hashes into the sites volume and re-creates the mismatch |
| Hashes still mismatch after `clear-cache`                                                                                         | The shared key outlived the site-level clear                                                                                                                                                                                                                             | `docker exec <redis-cache> redis-cli FLUSHALL` (cache-only Redis, holds nothing durable — `redis-queue` is the one with a volume, leave it alone), then restart `backend` and `frontend`                                       |
| Traefik `404 page not found` (plain text) on the domain                                                                           | No router matches the Host — domain changed but Coolify/Traefik labels didn't                                                                                                                                                                                            | Update the frontend service's domain in Coolify; keep https + letsencrypt                                                                                                                                                      |
| Frappe stack traces `AppNotInstalledError` / `'ErrorPage' object has no attribute 'app_path'` on random paths like `/config/.env` | Request Host resolves to no (or a half-provisioned) bench site; scanners trigger it constantly                                                                                                                                                                           | Set `FRAPPE_SITE_NAME_HEADER: <site>` on frontend; fix the site itself if it's every request                                                                                                                                   |
| Local build fails: `error getting credentials` on public images                                                                   | Docker Desktop's WSL credential helper (`credsStore: desktop.exe`) broken                                                                                                                                                                                                | Remove `credsStore` from `~/.docker/config.json`, `docker login ghcr.io` again                                                                                                                                                 |
| git commands resolve to a bizarre repo root (`/`, home dir)                                                                       | Stray `.git` directory in an ancestor (accidental `git init`)                                                                                                                                                                                                            | Find with `git rev-parse --show-toplevel` from the puzzled directory; delete the stray `.git`                                                                                                                                  |

## Release pipeline — staging → production

Two distinct states; know which one you're in:

**State A — production still runs the official `frappe/crm` image** (the
transition period). New fork changes (UI rework, BE changes) ship to
staging only. Production receives NOTHING until the one-time
**production cutover runbook** below is executed — there is no partial
promotion onto the official image: the first fork deployment to prod IS the
cutover, and it carries every fork change accumulated on staging up to that
tag. Practical consequence: keep the cutover close — the longer prod stays
official while the fork accumulates changes, the bigger and riskier that
first promotion becomes. Meanwhile, re-merge upstream `frappe/crm` develop
into the fork regularly so prod's rolling image can't drift ahead of you.

**State B — steady state after cutover** (prod runs the fork). Every
release is the same five stages:

| Stage        | Action                                                                                                                                                                                                                      | Gate                                    |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| 1. Integrate | PRs merge to `develop` (tests green per PR)                                                                                                                                                                                 | `yarn test:run` + review                |
| 2. Build     | `deploy/build.sh` → unique tag pushed to GHCR                                                                                                                                                                               | app-presence gate passes                |
| 3. Stage     | staging `FRAPPE_VERSION=<tag>` → redeploy → migrate + cache clear                                                                                                                                                           | post-deploy checklist on staging        |
| 4. Validate  | manual pass over CHANGED features + core flows (login, lead/deal list, kanban modal). For **schema-touching releases**: first refresh staging with prod data (restore runbook below) so `migrate` rehearses against reality | everything works with no console errors |
| 5. Promote   | prod `FRAPPE_VERSION=<the SAME tag>` → redeploy → migrate + cache clear                                                                                                                                                     | post-deploy checklist on prod           |

Rules that make this safe:

- **The tag promoted to prod is the tag staging validated** — never rebuild
  between stage 4 and 5; a rebuild is a new, unvalidated artifact.
- UI-only releases (no DocType/patch changes) can skip the prod-data
  refresh in stage 4 and need no maintenance window — `migrate` is a no-op.
- Schema-touching releases get the full stage-4 rehearsal and a maintenance
  window for stage 5, with a fresh prod backup taken first (rollback).
- One release in flight at a time; staging mirrors either prod or
  prod-plus-one-release, never a grab-bag of half-validated tags.

## Runbook — syncing upstream frappe/crm into the fork

Upstream moves fast (~dozens of commits/week on `develop`). Sync on a
schedule — small merges have small conflicts:

```bash
git checkout develop && git pull origin develop
git fetch upstream                       # upstream = https://github.com/frappe/crm.git
git merge upstream/develop
# resolve conflicts — expect them in files the fork customizes
# (ViewControls.vue, KanbanView.vue, dialogs.jsx, vite.config.js)
cd frontend && yarn test:run             # 142+ tests must stay green
git push origin develop
```

- Cadence: at least before every release, ideally weekly. (GitHub's "Sync
  fork" button does the same merge but resolves nothing — use the CLI when
  conflicts are likely.)
- The merge then rides the normal release pipeline (build → staging →
  promote); upstream code never reaches prod except as a validated tag.
- Watch upstream's frappe compatibility (README table): the image builds
  with `FRAPPE_BRANCH=version-15`. The day upstream `develop` requires
  frappe v16 is a coordinated framework upgrade (image + prod DB migration),
  not a routine sync — plan it, don't stumble into it.

## Runbook — restore production data into staging (rehearsal / refresh)

Used for: rehearsing a production cutover/migration on staging, and for
periodically refreshing staging with real data. Field-tested 2026-08-04
(prod `crm.txbconsulting.com` → staging `txb-crm.mygom-test.tech`).
Differing site names are irrelevant — a site name is a directory + config,
not data; restore replaces the DB contents wholesale.

1. **Backup prod** (inside prod backend container):

   ```bash
   bench --site crm.txbconsulting.com backup --with-files
   ```

   Produces 4 artifacts in `sites/<site>/private/backups/`:
   `*-site_config_backup.json` (contains the **encryption_key**),
   `*-database.sql.gz`, `*-files.tar`, `*-private-files.tar`.

2. **Copy them to the staging stack:**

   ```bash
   docker cp <prod-backend>:/home/frappe/frappe-bench/sites/<site>/private/backups/ ./prod-backup/
   # scp/rsync to the staging VPS if separate, then:
   docker cp ./prod-backup/. <staging-backend>:/home/frappe/frappe-bench/sites/
   ```

3. **Set prod's encryption_key on staging FIRST** (from the config backup
   json) — without it, every encrypted credential in the restored DB is
   unreadable:

   ```bash
   bench --site txb-crm.mygom-test.tech set-config encryption_key '<value>'
   ```

4. **Restore** (overwrites staging's DB — intended; may prompt for MariaDB
   root credentials since the database is recreated):

   ```bash
   bench --site txb-crm.mygom-test.tech --force restore \
     /home/frappe/frappe-bench/sites/<ts>-database.sql.gz \
     --with-public-files  /home/frappe/frappe-bench/sites/<ts>-files.tar \
     --with-private-files /home/frappe/frappe-bench/sites/<ts>-private-files.tar
   ```

5. **The rehearsal itself** — run prod's future migration on the fork image:

   ```bash
   bench --site txb-crm.mygom-test.tech migrate
   bench --site txb-crm.mygom-test.tech clear-cache
   bench --site txb-crm.mygom-test.tech clear-website-cache
   ```

   A clean migrate + working app (log in with a **prod** account — staging's
   users were just replaced) = the cutover is proven. A migrate failure here
   is a production outage caught early: fix, rebuild, re-run this runbook.

   `migrate` also runs `after_migrate`, which disables the 20 Server/Form
   Scripts prod currently runs on (`crm/txb/retired_scripts.py`). That swap —
   database scripts out, forked Python in — is the cutover's real behavioural
   moment, so exercise the flows they owned: lead owner assignment, duplicate
   lead block, deal call counts, contact/org sync, Take Action on a deal
   (exactly **one** menu), and the registration page.

6. Notes: staging now holds real customer data — same GDPR/access discipline
   as prod. Delete the backup artifacts from `sites/` afterwards.

## Runbook — refresh local dev from staging

Brings staging's data (including anything restored from prod) onto the
local dev bench for development against real-shaped data with HMR.

1. **Backup staging and fetch it:**

   ```bash
   docker exec <staging-backend> bench --site txb-crm.mygom-test.tech backup --with-files
   docker cp <staging-backend>:/home/frappe/frappe-bench/sites/txb-crm.mygom-test.tech/private/backups/ ./staging-backup/
   scp -r root@<staging-vps>:./staging-backup ~/
   ```

2. **Restore into the local site** (bench root; recreates the DB on the dev
   MariaDB — may prompt for DB root credentials):

   ```bash
   bench --site localhost set-config encryption_key '<key from site_config_backup.json>'
   bench --site localhost --force restore ./apps/crm/prod-backup/20260804_073332-crm_txbconsulting_com-database.sql.gz \
     --with-public-files  ./apps/crm/prod-backup/20260804_073332-crm_txbconsulting_com-files.tar \
     --with-private-files ./apps/crm/prod-backup/20260804_073332-crm_txbconsulting_com-private-files.tar
   bench --site localhost migrate
   ```

3. **MANDATORY, immediately after every restore — defuse the copied prod
   config** (the restored data contains prod's email accounts and scheduled
   jobs; without these flags a local `bench start` will pull and SEND REAL
   CUSTOMER EMAILS from your laptop):

   ```bash
   bench --site localhost set-config mute_emails 1
   bench --site localhost set-config pause_scheduler 1
   ```

4. Run as usual: `bench start` + `yarn dev` → `localhost:8080/crm`.
   Logins are now prod's (restore replaced all users — Administrator
   password is prod's admin password).
   - No `.env` files anywhere: Frappe config lives in
     `sites/common_site_config.json` (bench-wide) and
     `sites/localhost/site_config.json` (per-site), edited via
     `bench set-config`. The compose stack's env vars exist only to write
     these files inside containers.
   - This CANNOT affect staging: the restore writes a copy into the local
     site's database on the dev DB server; staging's database is on a
     different host and referenced nowhere in local config. Everything run
     as `bench --site localhost …` stays in the local sandbox.

5. Hygiene: this puts real EU customer data on a dev machine — full-disk
   encryption expected, delete `~/staging-backup/` after restoring, and
   never point the local bench directly at the staging/prod database
   (two code versions sharing one schema corrupt each other).

## Runbook — production cutover (official frappe/crm image → this fork)

Pre-conditions, in order — do not start without all three:

- **Version drift resolved**: prod's `bench --site all list-apps` versions
  are ≤ the fork's merged base (merge upstream `frappe/crm` develop into the
  fork if not; never deploy code older than the schema it meets), and the
  image was built with the frappe branch prod actually runs.
- **Rehearsal passed**: the restore-to-staging runbook above ran clean on
  the exact image tag being promoted.
- **Fresh prod backup** taken and copied off-VPS (this is the rollback).
- **Pre-cutover script snapshot** — capture what is live _before_ migrate
  disables anything, and paste it into the window notes. Variant A of the
  rollback needs this list, and by then the fork's module is gone with the
  image:

  ```bash
  docker exec <prod-backend> bench --site crm.txbconsulting.com console
  >>> frappe.db.get_all("CRM Form Script", {"enabled": 1}, pluck="name")
  >>> frappe.db.get_all("Server Script", {"disabled": 0}, pluck="name")
  ```

- **GHCR login on the prod VPS host**: `docker login ghcr.io -u <user> -p <PAT
with read:packages>`. The image is private and Coolify has no per-resource
  registry credential UI — it uses the host daemon's. Changing the image env
  var without this fails the pull, and the stack stays down on a tag it cannot
  fetch. Do this **before** the window, and prove it: `docker pull
ghcr.io/mygom-tech/txb-crm:<tag>` on the VPS.

Cutover (~15 min window):

1. Maintenance window announced; final `bench backup --with-files` on prod.
2. **Edit the existing Coolify resource in place** — never create a new one.
   The domain, its Traefik/Let's Encrypt labels and the `sites` volume all
   belong to that resource; a new one starts with an empty volume and no
   certificate, which is how you would lose both the data and the domain.
   Set `FRAPPE_IMAGE=ghcr.io/mygom-tech/txb-crm`,
   `FRAPPE_VERSION=<the staging-validated tag>` → Redeploy. (`create-site`,
   if the live stack has one, skips the existing site; prod data untouched.)
3. `bench --site all migrate && bench --site all clear-cache && bench --site all clear-website-cache`.
4. Run the post-deploy verification checklist (Phase 6) + smoke real flows
   (login, lead list, deal kanban + confirm modal, email if configured).
5. **Rollback path**: see the rollback runbook below. Decide _before_ the
   window which of the two variants you are willing to run.
6. Same-pass compose fixes if not already applied: migrate+cache-clear as
   post-deployment command, `redis-queue` data volume,
   `FRAPPE_SITE_NAME_HEADER: ${SITE_NAME}` on frontend, and confirm prod
   site config has no dev flags (`developer_mode`, `ignore_csrf`).

## Runbook — rollback

### What reverting the image does NOT undo

Migrations are forward-only; there is no `bench migrate --down`. Everything
`migrate` wrote to the database survives an image revert:

| Left behind                                                                   | Consequence on the official image                                                                |
| ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| **20 Server/Form Scripts disabled** (`crm/txb/retired_scripts.py`)            | **The dangerous one.** The fork's Python left with the image, so _neither_ implementation runs   |
| Registration tokens reissued (`reissue_registration_tokens`)                  | Old links stay dead. The revert also re-enables the script that mints predictable ones           |
| Custom fields (`add_ownership_custom_fields`, enrichment, product sync)       | Harmless — additive, and the official image ignores fields it does not reference                 |
| Backfilled record owners, `FCRM Settings` values, quick-entry layout reorders | Data edits. Harmless to the official code, but not reverted                                      |
| `tabPatch Log` rows                                                           | Re-cutting over later will **skip** those patches. `after_migrate` is what makes that survivable |

The fork adds **no new DocTypes** — verified against `upstream/develop`. That
is what makes the fast variant below viable: there are no orphan doctype rows
whose controllers vanished with the image.

### Variant A — fast revert, keep the data (app broken, data fine)

Use when the fork misbehaves but nothing has corrupted data. Minutes, no data
loss. Takes back the code, then repairs the one thing that does not repair
itself:

```bash
# 1. Coolify: FRAPPE_IMAGE/FRAPPE_VERSION back to the official image → Redeploy
# 2. Re-enable what after_migrate switched off, or the CRM silently loses
#    lead-owner assignment, duplicate blocking, call counts and contact sync
docker exec <backend> bench --site crm.txbconsulting.com console
>>> from crm.txb.retired_scripts import RETIRED_FORM_SCRIPTS, RETIRED_SERVER_SCRIPTS
```

The fork's module is gone with the image, so paste the two name lists in by
hand (keep a copy from `crm/txb/retired_scripts.py` in the window notes) and:

```python
for n in FORM_SCRIPTS:   frappe.db.set_value("CRM Form Script", n, "enabled", 1)
for n in SERVER_SCRIPTS: frappe.db.set_value("Server Script",  n, "disabled", 0)
frappe.db.commit(); frappe.clear_cache()
```

Then `bench --site all clear-cache && clear-website-cache` and smoke every
flow those scripts own. **Do not skip the re-enable** — the failure is silent:
no error, records just stop being processed.

### Variant B — full restore (data damaged, or Variant A does not settle it)

The sanctioned path, and the only one that undoes schema:

```bash
bench --site crm.txbconsulting.com set-config encryption_key '<from the config backup json>'
bench --site crm.txbconsulting.com --force restore <ts>-database.sql.gz \
  --with-public-files <ts>-files.tar --with-private-files <ts>-private-files.tar
```

Then repoint the image and redeploy. **Cost: every write since the step-1
backup is gone** — leads, deals, notes, calls. That is the real argument for a
short window and an announced freeze, not for a clever backup schedule.

### Choosing, under pressure

- Login broken, assets 404, an endpoint 500s → Variant A.
- A migration wrote wrong values, or you cannot explain what the data looks
  like → Variant B. Do not debug prod data on a hunch.
- Either way, take a **second** backup before rolling back. The broken state is
  evidence, and Variant B destroys it.

## Making changes — UI and backend, dev → prod

Every process runs from the same image, so backend/frontend/workers/scheduler
update atomically on redeploy. Quick reference:

| Change                                                               | Workflow                                                        |
| -------------------------------------------------------------------- | --------------------------------------------------------------- |
| Frontend (`frontend/src/...`)                                        | commit → `deploy/build.sh` → Coolify redeploy                   |
| Python code (API, hooks)                                             | same                                                            |
| Schema (DocType JSON, `patches.txt`)                                 | same **+ `bench --site all migrate`** (the post-deploy command) |
| New Python dep (`pyproject.toml`) / JS dep (`frontend/package.json`) | image rebuild covers it                                         |
| Server Scripts                                                       | DB-stored — no deploy at all                                    |

### UI change, step by step

1. `git checkout develop && git pull && git checkout -b feature/<name>`
   (or `fix/<name>`).
2. Run dev stack: `bench start` (bench root) + `yarn dev` (apps/crm) →
   edit `frontend/src/...`, HMR at `http://localhost:8080/crm`.
   The `yarn dev` log must say "Local frappe-ui vite plugin found".
3. Tests + hygiene: `cd frontend && yarn test:run`; prettier/eslint on
   touched files. Add unit tests for any new pure logic in `src/utils/`.
4. PR into `develop`, review, merge.
5. Release: `git checkout develop && git pull`, then
   `GH_TOKEN=<pat> ./deploy/build.sh` → note the sha tag.
6. Coolify: set `FRAPPE_VERSION=<sha>` → Redeploy. The migrate post-deploy
   command runs (no-op for pure UI — harmless).
7. Smoke-check the live page the change touches; hard-refresh if the PWA
   service worker clings to the old bundle.

### Backend change, step by step

All backend code lives in `crm/` (Python package in this repo). Where to edit:

| Goal                                  | Location                                                                                           |
| ------------------------------------- | -------------------------------------------------------------------------------------------------- |
| API endpoint (`call('crm.api.x.y')`)  | `crm/api/x.py`, function `y`, `@frappe.whitelist()` — dotted path == file path                     |
| Record business logic / validation    | `crm/fcrm/doctype/<name>/<name>.py` (e.g. `crm_deal.py` validate)                                  |
| DocType fields/schema                 | Desk UI on dev bench (developer_mode) → writes `crm/fcrm/doctype/<name>/<name>.json` — commit that |
| Doc events, scheduled jobs, overrides | `crm/hooks.py`                                                                                     |
| One-off data migration                | module in `crm/patches/` + line in `crm/patches.txt`                                               |
| Integrations                          | `crm/integrations/`                                                                                |
| Boot payload                          | `crm/www/crm.py`                                                                                   |
| Server tests                          | `crm/tests/`                                                                                       |

(`apps/frappe` is the framework — read it, never edit it; override via
`crm/overrides/` + `hooks.py`.)

1. Same branch flow as UI.
2. Run the dev stack; edit Python per the table above. The dev web server
   auto-reloads on save; workers and scheduler need a `bench start` restart
   to pick up code they import.
3. **Schema changes** only via the dev bench with its `developer_mode: 1`:
   create/edit DocTypes in the Desk UI at `localhost:8000/app` — that writes
   the DocType JSON into `crm/fcrm/doctype/...` for committing. Data
   backfills go in a patch module + `patches.txt` entry. (Prod has
   developer_mode off — DocTypes cannot and must not be edited there.)
4. Test: `bench --site localhost run-tests --app crm` for server tests
   (plus `yarn test:run` if the change touches API responses the frontend
   consumes). `bench --site localhost migrate` locally to prove patches run.
5. Merge → `deploy/build.sh` → pin sha in Coolify → Redeploy; the
   post-deploy `bench --site all migrate` applies schema/patches.
6. Verify: `docker exec <backend> bench version` shows the new build;
   exercise the changed endpoint; check `docker logs` on `queue-short`/
   `scheduler` for import errors after the restart.

## Appendix — Hetzner notes

- **CPU arch**: build.sh on an x86 dev machine produces an amd64 image — it
  will NOT run on Hetzner CAX (ARM) servers. Use CX/CPX (x86), or add
  buildx `--platform` to the script before choosing ARM.
- **DB networking**: if the MariaDB host is also Hetzner, attach both to a
  Hetzner private network and use the private IP as `DB_HOST`; otherwise
  firewall the DB port to the VPS IP. Do not ship the current dev pattern
  (public-internet MariaDB with plaintext creds).
- **Backups**: Hetzner Storage Box / Object Storage (EU region) as the
  off-VPS target for nightly `bench backup` artifacts.

## Appendix — GDPR notes (EU customer data in a CRM)

- Host VPS + DB + backups in EU regions; verify your S3 backup bucket region.
- The DB user/password and `encryption_key` are personal-data keys — Coolify
  secrets and password manager only, never in the repo.
- `bench --site <site> set-config` has data-retention helpers upstream; define
  a retention policy for Leads/Deals of lost prospects before someone asks.

## Appendix — Known fork-specific follow-ups that touch deployment

- `@framework/ui` dangling link (Phase 0.1) — delete when confirmed unused in build.
- Commit messages carry `Claude-Session` trailers — fine private, squash on
  any future open-sourcing.
- Dev bench flags (`developer_mode`, `ignore_csrf`) must never be copied into
  prod site config (Phase 5.3).

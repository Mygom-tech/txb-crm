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

| Item | Example |
|---|---|
| Production domain | `crm.example.com` |
| Site name (bench site = domain) | `crm.example.com` |
| Image | `ghcr.io/mygom-tech/txb-crm:latest` |
| Frappe branch | `version-15` (bench currently runs 15.116) |
| CRM branch to deploy | `develop` |

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
*once*, at site-creation time (Frappe creates the site's database and its own
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

**2.3 GHCR access** — in Coolify: *Settings → Registries* (or the project's
registry settings) add `ghcr.io` with a GitHub PAT that has `read:packages`,
so the VPS can pull the private image.

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
- The script prints a **token-leak check** — the app's git remote inside the
  image may retain the clone URL with the token. Expected mitigation: image
  stays private + PAT #1 is read-only + rotate it if a build artifact ever
  leaves GHCR. If the check shows the token and that's not acceptable,
  strip it in a follow-up image layer (`git remote set-url`).
- Pin `FRAPPE_BRANCH` (default `version-15` via the script) to whatever prod
  actually runs — see "Adapting the existing Coolify stack" step 1.
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
check *github.com/orgs/Mygom-tech/packages → txb-crm → Package settings* and
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
  image: ghcr.io/mygom-tech/txb-crm:latest
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

---

## Phase 6 — Deploy & operate

**Deploy flow** (every release): merge to `develop` → Action builds image →
in Coolify hit *Redeploy* (or enable auto-deploy via Coolify's GHCR webhook) →
then run migrations:

```bash
docker exec <backend> bench --site all migrate
```

Wire that as Coolify's **post-deployment command** so it's never forgotten.
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

## Adapting the EXISTING Coolify stack (crm.txbconsulting.com)

Production already runs the standard frappe_docker topology with
`FRAPPE_IMAGE`/`FRAPPE_VERSION` env vars (default `frappe/crm:develop`).
Deploying the fork is an image swap, not a new stack — Phases 2 and 4 above
are already done. The delta:

1. **Resolve version drift first.** `frappe/crm:develop` is a rolling tag —
   prod's DB schema tracks whatever upstream develop was at the last
   redeploy, which may be ahead of this fork's base commit. On the VPS:
   `docker exec <backend> bench version` → note frappe + crm versions. Merge
   upstream `frappe/crm` develop into the fork up to at least that commit,
   re-run the frontend test suite, and build the custom image with the
   **matching** `FRAPPE_BRANCH`. Never deploy app code older than the DB
   schema it meets.
2. Run **Phase 0** (clean-clone build test) and **Phase 3** (local
   `deploy/build.sh` → GHCR).
3. In Coolify: add GHCR registry credentials, set
   `FRAPPE_IMAGE=ghcr.io/mygom-tech/txb-crm` and `FRAPPE_VERSION=<sha-tag>`
   (pin by sha — rolling tags caused item 1), redeploy. The idempotent
   `create-site` service skips the existing site; data is untouched.
4. **Compose fixes to apply in the same pass:**
   - Add a migrate step — post-deployment command
     `docker exec <backend> bench --site all migrate` or a one-shot service
     after `create-site`. Today schema changes in a new image are never
     applied deliberately.
   - Add a data volume to `redis-queue` (queued jobs currently die with the
     container).
   - Set `FRAPPE_SITE_NAME_HEADER: ${SITE_NAME}` on `frontend` explicitly
     (today relies on site name == domain via the `$host` default).
5. Verify per Phase 5.3/Phase 6: prod site config has no dev flags, kanban
   confirm modal works on the live site, backups run and land off-VPS.

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

# TXB CRM — local dev bench from zero

How to stand up a working `txb-crm-be` bench on a new machine. This is the one
thing [`deployment-guide.md`](./deployment-guide.md) does not cover — that guide
starts from an image and a VPS.

**What is and is not in git.** Only two things here are custom code, and both
are versioned:

|                                                      | Repo                               |
| ---------------------------------------------------- | ---------------------------------- |
| The CRM app (all Python, all frontend, all DocTypes) | `Mygom-tech/txb-crm` — this repo   |
| The Gmail signature setter (unrelated to the CRM)    | `Mygom-tech/txb-google-signatures` |

Everything else in the bench directory is **generated**: `apps/frappe` is
pristine upstream, `env/` is a venv, `sites/assets/` is build output, and
`Procfile` / `patches.txt` / `config/redis_*.conf` are written by `bench init`
verbatim. Nothing there needs backing up — this document reproduces it.

The only things that cannot be recreated are secrets. See [Secrets](#secrets).

---

## 1. Prerequisites

Versions the current bench runs on (2026-08):

|           | Version                                             |
| --------- | --------------------------------------------------- |
| bench CLI | 5.31.0                                              |
| Python    | 3.11                                                |
| Node      | 22.x                                                |
| yarn      | 1.22 (classic — **not** berry)                      |
| Redis     | any 6+, local                                       |
| MariaDB   | 10.6+, reachable (the dev DB is remote — see below) |

```bash
pipx install frappe-bench     # or pip install --user frappe-bench
```

The dev bench does **not** run a local MariaDB; it points at
`195.201.127.51:3307`. You need that host reachable and its credentials from
the password manager. Redis, however, runs locally via `bench start`.

> This dev DB is on the public internet with plaintext credentials in
> `site_config.json`. It is a known wart, flagged in
> `deployment-guide.md` Phase 1.2 — do not replicate the pattern in prod.

## 2. Bench + apps

```bash
bench init --frappe-branch version-15 txb-crm-be
cd txb-crm-be
bench get-app crm https://github.com/Mygom-tech/txb-crm.git --branch develop
```

`bench init` writes `Procfile`, `patches.txt`, `config/redis_*.conf` and the
`env/` venv. Leave all of them alone; they are not customised.

## 3. Bench-wide config

`bench init` writes defaults. These are the values this bench actually runs
that differ from them — apply all of them:

```bash
bench set-config -g db_host 195.201.127.51
bench set-config -gp db_port 3307

# Non-default redis ports (the bench defaults are 13000/11000 only if nothing
# else claimed them — set explicitly so a second bench on the box cannot clash)
bench set-config -g redis_cache    redis://127.0.0.1:13000
bench set-config -g redis_queue    redis://127.0.0.1:11000
bench set-config -g redis_socketio redis://127.0.0.1:13000

bench set-config -gp socketio_port 9000
bench set-config -gp webserver_port 8000
bench set-config -gp file_watcher_port 6787

bench set-config -g  live_reload true
bench set-config -g  server_script_enabled true   # prod has DB-stored Server Scripts
bench set-config -gp gunicorn_workers 49
bench set-config -gp background_workers 1
```

Verify against `sites/common_site_config.json`.

## 4. Create the site

```bash
bench new-site localhost \
  --no-mariadb-socket \
  --db-host 195.201.127.51 --db-port 3307 \
  --db-root-username root --db-root-password '<from password manager>' \
  --admin-password '<choose>' \
  --install-app crm
```

Then the **dev-only** site flags. These belong on a laptop and must never
reach prod (`deployment-guide.md` Phase 5.3):

```bash
bench --site localhost set-config developer_mode 1   # required to edit DocTypes
bench --site localhost set-config ignore_csrf 1
bench --site localhost set-config allow_tests 1
```

`developer_mode` is not cosmetic: DocType edits in the Desk UI are how schema
changes get written into `crm/fcrm/doctype/*/*.json` for committing. Without
it there is no way to make a schema change in this project.

## 5. Get real data (optional, but usually the point)

Follow **"Runbook — refresh local dev from staging"** in
[`deployment-guide.md`](./deployment-guide.md). Do not improvise a restore;
that runbook exists because the naive version emails real customers.

### The two flags that are not optional

Immediately after **every** restore:

```bash
bench --site localhost set-config mute_emails 1
bench --site localhost set-config pause_scheduler 1
```

Restored prod data carries prod's email accounts and scheduled jobs. Without
these, `bench start` on your laptop pulls and **sends real customer email**.
Set them before the first `bench start` after a restore, not after.

Restoring also puts real EU customer data on the machine: full-disk encryption
expected, delete the backup artifacts afterwards, and never point the local
bench at the staging or prod database directly.

## 6. Run

```bash
bench start                    # bench root — web, socketio, workers, scheduler, redis
cd apps/crm && yarn dev        # separate terminal — vite HMR
```

→ `http://localhost:8080/crm`

The `yarn dev` log must say _"Local frappe-ui vite plugin found"_. If it does
not, HMR is silently serving stale bundles.

## 7. Verify

```bash
bench --site localhost list-apps            # → frappe, crm
curl -s localhost:8000/api/method/ping      # → {"message":"pong"}
bench --site localhost run-tests --app crm  # server tests
cd apps/crm/frontend && yarn test:run       # frontend tests
```

Log in, open the deal kanban, drag a card → the confirm modal appears.

---

## Secrets

None of this is in git, and none of it is derivable. Password manager only:

| Secret                   | Needed for                                                                                                                                                                  |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| MariaDB root password    | `bench new-site`, `bench restore`                                                                                                                                           |
| Site `encryption_key`    | Decrypting stored credentials in any restored backup. **Irreplaceable** — without the matching key, a restored DB's integration and email passwords are unreadable forever. |
| Admin password           | Login (after a restore it becomes prod's)                                                                                                                                   |
| `SA_KEY` (`sa-key.json`) | The signature tool, if you use it — see its own repo                                                                                                                        |

Frappe has no `.env` files. All config lives in
`sites/common_site_config.json` (bench-wide) and
`sites/localhost/site_config.json` (per-site), edited via `bench set-config`.

## Known gap — DocType customisations made in the Desk UI

`crm/hooks.py` declares **no fixtures**. Anything created through the Desk UI
that is not a DocType JSON — Custom Fields added by hand, Property Setters,
Server Scripts (prod stores 20 of them in the database) — exists only in the
database and is reproduced only by restoring a backup, never by this document.

Code reproducibility is solved. Site-configuration reproducibility depends
entirely on having a backup. If that ever needs to change, the fix is a
`fixtures` list in `hooks.py`; it is deliberately not there today because the
restore-based workflow covers it.

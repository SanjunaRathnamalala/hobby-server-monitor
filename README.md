# Hobby Server Monitor

An admin dashboard and control panel for an LXD host. Admins create, resize,
monitor and delete containers, invite users and give them resource quotas.
Users see and use only the containers assigned to them. A separate collector
stores metrics every 10 seconds in TinyFlux.

## 1. Setup (fresh Ubuntu 24.04)

### 1.1 LXD

```bash
sudo snap install lxd --channel=5.21/stable
sudo usermod -aG lxd $USER && newgrp lxd
lxd init --minimal
# The default "dir" pool cannot measure or limit disk size, so use btrfs:
lxc storage create pool1 btrfs size=20GiB
lxc profile device set default root pool pool1
# Containers are created only from local images (see REPORT, limitations):
lxc image copy ubuntu:24.04 local: --alias ubuntu-24.04
```

### 1.2 Google OAuth credentials

1. Open console.cloud.google.com and create a project.
2. Google Auth Platform → Get started: app name, audience **External**, contact email.
3. Audience → Test users: add every Google account that will sign in.
4. Clients → Create client → **Web application**. Authorized redirect URI:
   `http://localhost:8100/api/auth/callback`
5. Copy the client ID and secret into `.env` (next step).

### 1.3 Application

```bash
git clone <this-repo-url> hobby-server-monitor && cd hobby-server-monitor
cp .env.example .env        # fill in the Google values and BOOTSTRAP_ADMIN_EMAIL
sudo apt install -y python3-venv
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
set -a; source ../.env; set +a
python -m api.init_db       # creates data/hsm.db, safe to run again
cd ../dashboard && npm install && npm run build && cd ../backend
```

Run the two processes (each in `backend/`, with the venv active and `.env` loaded):

```bash
python -m collector
gunicorn --bind 127.0.0.1:8100 --workers 1 --threads 4 api.app:app
```

Open http://localhost:8100 and sign in with the `BOOTSTRAP_ADMIN_EMAIL` account.
Keep **one** gunicorn worker: the lock that prevents double-booking quotas
lives in the process's memory.

## 2. Architecture

```
 Browser ── HTTP ──> Falcon API (gunicorn, 1 worker, 127.0.0.1:8100)
                      │  middleware 1: one SQLite connection per request
                      │  middleware 2: authorization (deny by default)
                      ├── SQLite: users, sessions, containers, assignments, audit_log
                      ├── reads snapshot.json + TinyFlux files (metrics)
                      ├── pylxd: actions only (create, resize, start/stop, delete, exec)
                      └── serves the built Astro dashboard (static files)

 Collector (separate process, every 10 s)
   pylxd ──> CPU %, network rates ──> TinyFlux: hourly raw files + daily 5-min summaries
                                  └─> snapshot.json (latest state, atomic replace)
```

- The **collector** is the only component that polls LXD for metrics. It runs
  whether or not a browser is open, and keeps retrying if LXD is down.
- The **API** never polls LXD for metrics. It reads the snapshot (live view) and
  the TinyFlux files (history), so the cost does not grow with open browser tabs.
- The **dashboard** is one static page plus one JavaScript file. It polls
  `/api/containers` every 10 s, only while the tab is visible.

## 3. Data model

### SQLite (`backend/api/db.py`, schema version in `PRAGMA user_version`)

| Table | Columns | Notes |
| --- | --- | --- |
| users | id, email (unique, case-insensitive), role (admin/user), quota_mem (bytes), quota_cpu (cores), quota_disk (bytes), created_at | |
| sessions | token_hash (SHA-256), user_id → users ON DELETE CASCADE, expires_at | the raw token exists only in the cookie |
| containers | uuid (LXD volatile.uuid) PK, owner_id → users ON DELETE SET NULL | the owner's quota pays for the container |
| assignments | user_id → users CASCADE, container_uuid → containers CASCADE | who may see/use which container |
| audit_log | time, actor_email, action, target, details (JSON) | no foreign keys, survives deletions |

### TinyFlux (`backend/data/`)

- Measurement `ct`, tag `id` = LXD `volatile.uuid` (it survives renames).
- Fields: `cpu` (cores in use), `mem` / `mem_max` (bytes), `disk` / `disk_max`
  (bytes), `rx` / `tx` (bytes per second), `procs`.
- `raw/YYYY-MM-DDTHH.csv`: one point per running container every 10 s, kept 24 h.
- `summary/YYYY-MM-DD.csv`: 5-minute averages, kept 30 days.
- Old data is removed by deleting whole files. Stopped containers are not stored.
- `snapshot.json`: latest state of all containers plus collector status.

## 4. API reference

All bodies are JSON. Sizes are in **bytes**, CPU in **cores**. Errors look like
`{"title": "403 Forbidden", "description": "..."}`. Requests that change data
must come from `APP_ORIGIN` (CSRF protection).

| Method | Path | Role | Body / result |
| --- | --- | --- | --- |
| GET | /api/health | public | `{"status":"ok"}` |
| GET | /api/auth/login | public | redirect to Google |
| GET | /api/auth/callback | public | sets session cookie, redirects to `/` |
| POST | /api/auth/logout | public | 204 |
| GET | /api/me | user | id, email, role |
| GET | /api/containers | user | collector status + containers (users: assigned only) |
| POST | /api/containers | admin | name, image, pool, network, mem, cpu, cpu_allowance, disk, owner_id?, ephemeral, autostart, description → 201 |
| GET | /api/containers/options?owner=ID | admin | images, pools, networks, owners, bounds for this owner |
| GET | /api/containers/{id} | user (assigned) | one container |
| DELETE | /api/containers/{id}?confirm=NAME | admin | 204 |
| GET | /api/containers/{id}/history?range=15m\|1h\|6h\|24h\|7d\|30d | user (assigned) | columns `t, cpu, mem, mem_max, disk, disk_max, rx, tx, procs` (max 500 points) |
| POST | /api/containers/{id}/actions | admin | `{"action": start\|stop\|force_stop\|restart\|freeze\|unfreeze}` |
| PATCH | /api/containers/{id}/limits | admin | any of mem, cpu, cpu_allowance, disk |
| POST | /api/containers/{id}/exec | user (assigned) | `{"command": "..."}` → exit_code, output, timed_out |
| GET / POST | /api/users | admin | list / invite (email, role, quota_mem, quota_cpu, quota_disk) |
| PATCH / DELETE | /api/users/{id} | admin | change role or quotas / revoke |
| PUT / DELETE | /api/users/{id}/containers/{container_id} | admin | grant / remove access |
| GET | /api/capacity | admin | host totals, allocations, per-user allocated vs quota |
| GET | /api/quota | user | own quota and allocated |

Status codes: 400 invalid input, 401 not signed in, 403 not allowed (including
unassigned containers), 404 not found, 409 conflict (LXD refused, not enough
capacity or quota), 503 LXD unreachable.

## 5. Security notes

**Threats considered:** an uninvited Google user; an invited user trying to reach
other users' containers or the host; another website sending requests with the
admin's cookie (CSRF); a leaked database file; abuse of the terminal.

**Controls:**
- Login only through Google, with PKCE and a `state` check; the email must be verified.
  Uninvited emails are refused, and no account is created.
- Server-side sessions: random 256-bit token in an HttpOnly, Secure, SameSite=Lax
  cookie; only its SHA-256 hash is stored. Logout and revoking delete sessions at once.
- Authorization in one middleware, deny by default: every route must declare
  `public`, `user` or `admin`, or it is refused. Any URL with `{container_id}` is
  checked against assignments automatically (403).
- Changing requests must carry our `Origin`.
- All SQL uses placeholders. All input is validated on the server (types, ranges,
  unknown fields refused). The dashboard inserts data only as text (no XSS).
- Terminal: commands run only inside the container, through LXD; 10 s timeout,
  64 KB output, the command passed as an argument (never pasted into our shell
  text), and every command is in the audit log. Created containers get
  `limits.processes=500` against fork bombs.

**LXD privilege decision.** Access to the LXD socket is equivalent to root on the
host: whoever can talk to LXD can create a privileged container that mounts the
host's filesystem. The API and the collector need this access. What we did:
users never talk to LXD; the API performs only fixed operations; the create form
can never set `security.privileged`, `raw.*`, host devices or nesting; only
LXD-managed bridges, limit-capable pools and local images are offered;
containers stay unprivileged (root inside = UID 1000000 on the host); the API
listens on 127.0.0.1 only. What we would add next (not done, see REPORT): an LXD
project with `restricted=true` and a restricted TLS client certificate for the
API (and a separate one for the collector), a dedicated system user, and an HTTPS
reverse proxy.

## 6. Configuration (`.env`)

| Variable | Meaning |
| --- | --- |
| GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET | OAuth client from Google Cloud |
| OAUTH_REDIRECT_URI | must match the URI registered at Google |
| BOOTSTRAP_ADMIN_EMAIL | becomes admin on first login, only while no admin exists |
| APP_ORIGIN | the dashboard's address; changing requests from other origins are refused |
| HSM_DATA_DIR | TinyFlux files and snapshot.json (default `data`) |
| HSM_DB_PATH | SQLite file (default `data/hsm.db`) |
| COOKIE_SECURE | `true` (browsers accept Secure cookies on http://localhost) |

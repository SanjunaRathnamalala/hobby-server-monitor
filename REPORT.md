# Report — Hobby Server Monitor

## Time spent (approximate)

| Area | Hours |
| --- | --- |
| Learning LXD, storage pools, pylxd | 3(Also on the go) |
| Collector + TinyFlux design | 3 |
| Backend: auth, middleware, endpoints | 3 |
| LXD integration: actions, create, limits, terminal | 3 |
| Frontend (Astro dashboard) | 1.5 |
| Debugging and testing | 1 |
| Documentation, measurement, video | 1.5 |

## Decisions (the open questions in the task)

1. **Staying signed in / logout.** Server-side sessions in SQLite. The cookie holds
   a random 256-bit token (HttpOnly, Secure, SameSite=Lax, 12 h); the database
   stores only its SHA-256 hash. Logout deletes the row, so the token stops
   working immediately. Rejected: JWT, because it cannot be revoked before it
   expires, and the task requires revoking users.
2. **Where authorization happens.** In one Falcon middleware, before any route
   code runs. Every route declares `auth = "public" | "user" | "admin"` (or per
   method); a missing or misspelled rule is refused and logged. Any route with
   `{container_id}` gets the assignment check automatically, so a new endpoint
   cannot forget it. Non-admins get 403 (not 404) for unknown containers, so IDs
   cannot be probed. Rejected: checks inside each route (easy to forget once).
3. **What a quota measures.** The limits allocated to the containers a user owns
   (RAM, cores, disk), not their current usage, because usage changes every second
   and could pass the quota later. At the limit, creating or enlarging is refused
   with a clear message; running containers are never stopped. Creation is also
   bounded by host capacity (minus a 1 GiB reserve for the host). Quotas may add
   up to more than the host (overcommitting budgets is safe); real allocations
   cannot. Considered: overcommitting container limits (common in clouds). It is
   fine for CPU, risky for disk, dangerous for RAM (OOM killer), and the task asks
   for bounds from real host capacity, so it was not done.
4. **pylxd's privilege.** See README, "LXD privilege decision". Summary: LXD
   access = root on the host; we contain it by never exposing LXD to users,
   allowing only fixed operations and safe options, keeping containers
   unprivileged, and binding the API to localhost. Restricted LXD projects and
   certificates are the next step (not done).
5. **How the dashboard learns about changes.** It polls `/api/containers` every
   10 s, only while the tab is visible. The API answers from `snapshot.json`
   written by the collector, so many tabs cost almost nothing, and a forgotten
   tab costs nothing. Rejected: WebSockets/SSE (an open connection per tab, more
   code, and data changes only every 10 s anyway). Trade-off: the list can be up
   to 10 s behind after an action, but the action's answer is shown at once.
6. **The metric store after a month.** Hourly raw files kept 24 h and daily files
   of 5-minute averages kept 30 days; old data is removed by deleting whole files,
   not rows. A measured row is about 190 bytes, so 10 containers use at most about
   50 MB in total, forever. Stopped containers are not stored.
7. **How real the terminal is.** A real `pylxd` exec of one command at a time (not
   an interactive shell). Limits: 10 s, 64 KB output, the command passed as `$1`
   to a fixed wrapper, 500 processes per container, and an audit entry for every
   command. Users are root inside their own container, which is intended. Not
   intended but possible: a determined user could get around the output cap (e.g.
   by writing to a parent process's output); the timeout still applies, and
   nothing reaches the host.
8. **Rename and delete.** Containers are identified by LXD's `volatile.uuid`. I
   tested that it stays the same after `lxc rename`, so history and assignments
   survive renames. Deleting through the API removes the row, and its assignments
   go by `ON DELETE CASCADE`; the audit log keeps the record. Gap: containers
   deleted outside the API (e.g. ephemeral ones that stop) leave a stale row; they
   disappear from the snapshot, so nobody sees them.
9. **Data for a 24-hour chart.** 288 points from the 5-minute summaries (any range
   is capped at 500 points by averaging), sent as columns instead of objects.
   Measured: a 1-hour answer was 14,665 bytes, and 13,709 bytes after switching to
   compact JSON. gzip in a reverse proxy would shrink it much further (not done).
10. **When LXD is down, slow, or strange.** The collector logs one warning, keeps
    retrying every round, and writes the error into the snapshot; the dashboard
    then shows "LXD is not responding" with the last known data, or "Monitoring is
    not running" if the snapshot is older than 30 s. LXD calls have timeouts (5 s
    collector, 45 s API) and answer 503. Unexpected values (missing disk data,
    unknown limit formats) become "—" or "no limit", never a crash. Tested with
    `snap stop lxd`.
11. **The first admin.** `BOOTSTRAP_ADMIN_EMAIL`. That Google account (verified
    email) becomes admin on first login, but only while no admin exists, so a
    revoked bootstrap admin cannot regain admin by logging in again. Rejected:
    "first user becomes admin", which is a race anyone can win.
12. **Running on the machine and after reboot.** Currently two processes started
    by hand (see README). LXD restarts containers by itself; history survives in
    files. Not done: systemd units (gap; see limitations).

## Issues encountered and solutions

- Ran `lxc` inside a container by mistake: it tried to install LXD there. Learned
  that a container cannot control LXD (a useful security property).
- `lxd init --minimal` created a `dir` pool: disk usage 0 B and no size limits.
  Switched to a btrfs pool.
- Disk usage seemed wrong after writing 100 MB: btrfs updates its numbers about
  every 30 s. `lxc info` also prints usage as the total; the raw API was correct.
- Reading all containers in one call (`recursion=2`) was slower than one call per
  container with one container. Kept the simpler method; to re-test with more.
- The template's `.gitignore` was empty, so `.venv` and `.env` could have been
  committed. Fixed first.
- TinyFlux builds its index by reading the whole file when it opens it, so one
  large file would make every chart slow. Used small hourly and daily files.
  TinyFlux also stores numbers as floats (`100.0`).
- Raising a redirect in Falcon would have rolled back the new login session
  (our middleware rolls back on exceptions). Redirects set the status instead.
- pylxd's `stop()` uses `force=True` by default (unlike `lxc stop`); we pass
  `force=False` for graceful stops and offer a separate force stop.
- Testing from the Firefox console failed: the JSON viewer page has a strict
  CSP. Switched to curl tests.
- Errors were shown as XML in browsers (content negotiation); made errors JSON only.
- Another project used port 8000 during an evaluation; moved to 8100.

## What I learned

LXD - A system container manager. A container is a full Linux system, but it shares the host's kernel instead of running its own like a VM, so it is much lighter than a virtual machine.

cgroups - Linux kernel feature that manages resources between multiple applications. LXD heavily relies on these cgroups to manage resources between applications.

Unprivileged containers - root inside the container is mapped to an unprivileged user ID on the host (UID 1000000 here). If an attacker escapes the container, they are only an ordinary user on the host, not root, so the damage is much smaller.

Authentications:
* Sessions(stateful) - When logged in for a server, it writes a record on the database and gives browser a random ID(a cookie). Every time move between webpages on that server, browser sends the ID, server do the database lookup and permissions are granted, no need for loggings again.
* JWT(stateless) - When logged in, server gives a signed, tamper-proof token containing user details. When moving between links, server mathematically verifies the token skipping the databases lookups, saving time.

Web Security:
* XSS(Cross-Site Scripting) - The attacker targets the user. They find a way to inject malicious JavaScript into a legitimate website. When user view the page, browser run those malicious scripts and might provide an attacker with user's session cookies
* CSRF (Cross-Site Request Forgery) - A malicious website makes the user's browser send a request to our server. The browser attaches the user's cookie automatically, so the server thinks the user sent it. Our protection: SameSite cookies and checking the Origin header on every changing request.

## Bonus features

Audit log for every change and command; CSRF protection by Origin; PKCE;
process limit against fork bombs; per-user usage accounting; 30-day history
with downsampling; compact JSON; dashboard served by the API (one process,
no separate web server).

## Resource measurements (idle, no browser open)

Measured on an ASUS laptop (Intel i5 10th gen, 8 threads, 8 GiB RAM, Ubuntu 24.04)
with 2 containers.

| Process | RAM (RSS) | CPU (average over 60 s) |
| --- | --- | --- |
| collector | 17.0 MiB | 0.13 % |
| gunicorn master | 25.8 MiB | 0.03 % |
| gunicorn worker (API) | 49.2 MiB | 0.02 % |
| **Total** | **about 92 MiB** | **about 0.2 %** |

Storage after the test period: raw files 760 KB (3,740 rows), summaries 68 KB,
SQLite 48 KB; dashboard build 28 KB in total.

## Known limitations

- Not done: systemd units, HTTPS reverse proxy and gzip, LXD restricted project
  and certificates, automated tests and CI, creating from internet images
  (local images only, because downloads take minutes; the fix is creating in the
  background with progress), CPU overcommit setting.
- One gunicorn worker is required (the quota lock is in process memory).
- Failed actions are logged to the server log, not the audit table.
- The terminal output cap can be bypassed by a determined user (see decision 7).
- Ephemeral containers deleted by LXD leave a stale database row.
- A 5-minute summary is lost if the collector is killed in the middle of a block
  (a normal stop writes it). Averages hide short spikes.
- The CPU allowance percentage is a soft limit in LXD (applies only when the host
  is busy).

## AI tool usage

I used Claude (Anthropic) as a tutor and pair programmer
for the whole task. It explained LXD, OAuth, sessions and the other concepts, helped me map my simple ideas into real SE methodologies and refine them. Every endpoint was tested with
curl or the browser before moving on. 

Things that went wrong and were fixed with help of AI: 
* test scripts that printed thousands of lines in the Python prompt
* placeholder values left in test commands
* the Firefox console being blocked by CSP
* the inconsistent error JSON.
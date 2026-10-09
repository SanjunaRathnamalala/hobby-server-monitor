// Dashboard logic. All data is inserted as text (textContent), never as HTML,
// so names or descriptions typed by users cannot run as code (XSS).

const app = document.getElementById("app");
const who = document.getElementById("who");
const MiB = 2 ** 20, GiB = 2 ** 30;
let me = null, containers = [], selected = null, range = "1h";
const listBox = el("section", { class: "card" });
const detailBox = el("section", { class: "card" });
const extraBox = el("div", { class: "grid-wide" });

// ---------- helpers ----------
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined) node.append(c);
  return node;
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const data = r.status === 204 ? null : await r.json().catch(() => null);
  if (!r.ok) throw new Error((data && (data.description || data.title)) || `HTTP ${r.status}`);
  return data;
}

const bytes = n => n == null ? "—" : n >= GiB ? (n / GiB).toFixed(1) + " GiB"
  : n >= MiB ? Math.round(n / MiB) + " MiB" : Math.round(n / 1024) + " KiB";
const rate = n => n == null ? "—" : bytes(n) + "/s";
const pct = (a, b) => a == null || !b ? "—" : Math.round((a / b) * 100) + "%";
function duration(s) {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}
function notify(msg, isError = false) {
  const n = document.getElementById("notice");
  if (n) { n.className = isError ? "error" : "info"; n.textContent = msg; }
}
async function run(fn) { try { await fn(); } catch (e) { notify(e.message, true); } }

// ---------- start, login, logout ----------
const LOGIN_ERRORS = {
  cancelled: "Sign-in was cancelled.",
  failed: "Sign-in failed. Please try again.",
  not_invited: "This Google account has not been invited by an admin.",
};

async function start() {
  try { me = await api("GET", "/api/me"); }
  catch { return showLogin(new URLSearchParams(location.search).get("login_error")); }
  who.replaceChildren(`${me.email} (${me.role}) `, el("button", { text: "Sign out", onclick: logout }));
  detailBox.hidden = true;
  app.replaceChildren(el("p", { id: "notice" }), listBox, detailBox, extraBox);
  refresh();
  if (me.role === "admin") renderAdmin(); else renderQuota();
  // Poll only while the tab is visible: a hidden tab costs the server nothing.
  setInterval(() => { if (document.visibilityState === "visible") refresh(); }, 10000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refresh();
  });
}

function showLogin(error) {
  app.replaceChildren(el("section", { class: "card login" },
    el("p", { text: "Sign in to manage and monitor your containers." }),
    error ? el("p", { class: "error", text: LOGIN_ERRORS[error] || "Sign-in failed." }) : null,
    el("a", { class: "button", href: "/api/auth/login", text: "Sign in with Google" })));
}

async function logout() {
  await api("POST", "/api/auth/logout").catch(() => {});
  location.href = "/";
}

// ---------- container list ----------
async function refresh() {
  let data;
  try { data = await api("GET", "/api/containers"); }
  catch (e) {
    listBox.replaceChildren(el("p", { class: "error", text: "Could not load containers: " + e.message }));
    return;
  }
  containers = data.containers;
  const s = data.status;
  const warning = s.collector === "no_data" ? "Monitoring has not collected any data yet."
    : s.collector !== "ok" ? "Monitoring is not running: the numbers below may be old."
    : !s.lxd_ok ? "LXD is not responding: showing the last known data." : null;
  const head = ["Name", "State", "CPU (cores)", "RAM", "Disk", "Network", "Procs", "Uptime", "IPv4", "Image"];
  const rows = containers.map(c => el("tr", { class: "click" + (c.id === selected ? " sel" : ""), onclick: () => select(c.id) },
    el("td", { text: c.name }),
    el("td", {}, el("span", { class: "st " + c.status.toLowerCase(), text: c.status })),
    el("td", { text: c.cpu == null ? "—" : c.cpu.toFixed(2) }),
    el("td", { text: `${bytes(c.mem)} / ${bytes(c.mem_max)} (${pct(c.mem, c.mem_max)})` }),
    el("td", { text: `${bytes(c.disk)} / ${bytes(c.disk_max)} (${pct(c.disk, c.disk_max)})` }),
    el("td", { text: `↓ ${rate(c.rx)}  ↑ ${rate(c.tx)}` }),
    el("td", { text: c.procs ?? "—" }),
    el("td", { text: duration(c.uptime) }),
    el("td", { text: c.ipv4 || "—" }),
    el("td", { text: c.image || "—" })));
  listBox.replaceChildren(
    el("h2", { text: "Containers" }),
    warning ? el("p", { class: "error", text: warning }) : null,
    el("p", { class: "muted", text: "Last update: " + (s.last_success ? new Date(s.last_success).toLocaleTimeString() : "never")
      + ". Click a row for history" + (me.role === "admin" ? ", actions" : "") + " and terminal." }),
    containers.length
      ? el("div", { class: "scroll" }, el("table", {},
          el("thead", {}, el("tr", {}, head.map(h => el("th", { text: h })))), el("tbody", {}, rows)))
      : el("p", { text: me.role === "admin" ? "No containers yet. Create one below." : "No containers are assigned to you yet." }));
  if (selected && (range === "15m" || range === "1h")) drawChart();
}

// ---------- detail: chart, actions, terminal ----------
function select(id) {
  selected = id;
  const c = containers.find(x => x.id === id);
  if (!c) return;
  const rangeSelect = el("select", { onchange: e => { range = e.target.value; drawChart(); } },
    ["15m", "1h", "6h", "24h", "7d", "30d"].map(r => el("option", { value: r, text: r })));
  rangeSelect.value = range;
  detailBox.replaceChildren(
    el("h2", { text: c.name }),
    el("p", {}, "History: ", rangeSelect),
    el("div", { id: "chart", class: "chart", text: "Loading history…" }),
    me.role === "admin" ? adminControls(c) : null,
    terminal(c));
  detailBox.hidden = false;
  drawChart();
  refresh();
  detailBox.scrollIntoView({ behavior: "smooth" });
}

async function drawChart() {
  const box = document.getElementById("chart");
  if (!box || !selected) return;
  let h;
  try { h = await api("GET", `/api/containers/${selected}/history?range=${range}`); }
  catch (e) { box.textContent = "Could not load history: " + e.message; return; }
  if (!h.t.length) { box.textContent = "No history for this range yet."; return; }
  const memPct = h.mem.map((m, i) => m == null || !h.mem_max[i] ? null : (m / h.mem_max[i]) * 100);
  const cpuTop = Math.max(0.01, ...h.cpu.filter(v => v != null));
  const cpuPct = h.cpu.map(v => v == null ? null : (v / cpuTop) * 100);
  box.replaceChildren(
    svgChart(h.t, [[memPct, "#2a7"], [cpuPct, "#36c"]]),
    el("p", { class: "muted" },
      el("span", { class: "key-mem", text: "■ RAM % of limit" }), "   ",
      el("span", { class: "key-cpu", text: `■ CPU (top of chart = ${cpuTop.toFixed(2)} cores)` }),
      `   ${h.t.length} points, from ${new Date(h.t[0] * 1000).toLocaleString()}`));
}

function svgChart(t, series) {
  const NS = "http://www.w3.org/2000/svg", W = 600, H = 150;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  const t0 = t[0], span = Math.max(1, t[t.length - 1] - t0);
  const gap = (3 * span) / Math.max(1, t.length - 1); // a longer pause = no data (stopped)
  for (const [values, color] of series) {
    let d = "", pen = false;
    values.forEach((v, i) => {
      if (v == null || (i > 0 && t[i] - t[i - 1] > gap)) pen = false;
      if (v == null) return;
      const x = ((t[i] - t0) / span) * W;
      const y = H - 2 - (Math.min(v, 100) / 100) * (H - 4);
      d += (pen ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
      pen = true;
    });
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", color);
    path.setAttribute("stroke-width", "1.5");
    path.setAttribute("vector-effect", "non-scaling-stroke");
    svg.append(path);
  }
  return svg;
}

function adminControls(c) {
  const box = el("div", { class: "row" });
  for (const a of ["start", "stop", "restart", "freeze", "unfreeze", "force_stop"]) {
    box.append(el("button", { text: a.replace("_", " "), onclick: () => run(async () => {
      if (a === "force_stop" && !confirm("Force stop kills all programs without a clean shutdown. Continue?")) return;
      notify(`${c.name}: ${a.replace("_", " ")}…`);
      await api("POST", `/api/containers/${c.id}/actions`, { action: a });
      notify(`${c.name}: ${a.replace("_", " ")} done. The list updates within 10 seconds.`);
      refresh();
    }) }));
  }
  box.append(el("button", { class: "danger", text: "Delete", onclick: () => run(async () => {
    const typed = prompt(`This deletes ${c.name} and all its data. Type the name to confirm:`);
    if (typed === null) return;
    await api("DELETE", `/api/containers/${c.id}?confirm=${encodeURIComponent(typed)}`);
    notify(`${c.name} deleted.`);
    selected = null; detailBox.hidden = true; refresh();
  }) }));

  const mem = el("input", { type: "number", min: "128", placeholder: "RAM MiB" });
  const cpu = el("input", { type: "number", min: "1", placeholder: "cores" });
  const disk = el("input", { type: "number", min: "2", placeholder: "disk GiB" });
  const limits = el("form", { class: "row", onsubmit: e => { e.preventDefault(); run(async () => {
    const body = {};
    if (mem.value) body.mem = Math.round(Number(mem.value) * MiB);
    if (cpu.value) body.cpu = Number(cpu.value);
    if (disk.value) body.disk = Math.round(Number(disk.value) * GiB);
    await api("PATCH", `/api/containers/${c.id}/limits`, body);
    notify(`${c.name}: limits changed.`); refresh();
  }); } }, el("strong", { text: "Change limits:" }), mem, cpu, disk, el("button", { text: "Apply" }));
  return el("div", {}, box, limits);
}

function terminal(c) {
  const out = el("pre", { class: "term", text: "Output appears here. Each command may run for at most 10 seconds." });
  const input = el("input", { type: "text", class: "wide", placeholder: "command, for example: df -h" });
  return el("div", {},
    el("h3", { text: "Terminal" }),
    el("form", { class: "row", onsubmit: e => { e.preventDefault(); run(async () => {
      const cmd = input.value;
      if (!cmd.trim()) return;
      out.textContent = `$ ${cmd}\n(running…)`;
      const r = await api("POST", `/api/containers/${c.id}/exec`, { command: cmd });
      out.textContent = `$ ${cmd}\n${r.output}${r.timed_out ? "\n[stopped after 10 seconds]" : ""}\n[exit code ${r.exit_code}]`;
    }); } }, input, el("button", { text: "Run" })),
    out);
}

// ---------- admin: create form, usage, users ----------
function renderAdmin() {
  const create = el("section", { class: "card" });
  const people = el("section", { class: "card" });
  extraBox.replaceChildren(create, people);
  renderCreate(create, "");
  renderUsers(people);
}

function slider(label, key, min, max, unit, unitName, initial) {
  const lo = Math.ceil(min / unit), hi = Math.floor(max / unit);
  const input = el("input", { type: "range", min: String(lo), max: String(Math.max(lo, hi)), value: String(initial ?? lo) });
  const show = el("span", { text: `${input.value} ${unitName}` });
  input.addEventListener("input", () => { show.textContent = `${input.value} ${unitName}`; });
  input.disabled = hi < lo;
  const wrap = el("label", {}, `${label}: `, input, show,
    hi < lo ? el("span", { class: "error", text: "not enough left" }) : el("span", { class: "muted", text: `(max ${hi})` }));
  return { node: wrap, key, read: () => Number(input.value) * unit };
}

async function renderCreate(box, owner) {
  let o;
  try { o = await api("GET", "/api/containers/options" + (owner ? `?owner=${owner}` : "")); }
  catch (e) { box.replaceChildren(el("p", { class: "error", text: "Could not load form options: " + e.message })); return; }
  const ownerSelect = el("select", { onchange: e => renderCreate(box, e.target.value) },
    el("option", { value: "", text: "No owner (only host limits apply)" }),
    o.owners.map(u => el("option", { value: String(u.id), text: u.email })));
  ownerSelect.value = owner;
  const name = el("input", { required: "", maxlength: "63", pattern: "[a-z]([a-z0-9\\-]{0,61}[a-z0-9])?", placeholder: "my-container" });
  const image = el("select", {}, o.images.map(i => el("option", { value: i.fingerprint, text: i.description })));
  const pool = el("select", {}, o.pools.map(p => el("option", { value: p.name, text: `${p.name} (up to ${bytes(p.max_disk)})` })));
  const network = el("select", {}, o.networks.map(n => el("option", { value: n, text: n })));
  const ephemeral = el("input", { type: "checkbox" });
  const autostart = el("input", { type: "checkbox" });
  const description = el("input", { maxlength: "200", class: "wide", placeholder: "optional note" });
  const sliders = [
    slider("RAM", "mem", o.mem.min, o.mem.max, MiB, "MiB"),
    slider("CPU cores", "cpu", o.cpu.min, o.cpu.max, 1, "cores"),
    slider("CPU allowance", "cpu_allowance", 1, 100, 1, "%", 100),
    slider("Disk", "disk", o.disk_min, o.pools.length ? o.pools[0].max_disk : 0, GiB, "GiB"),
  ];
  box.replaceChildren(
    el("h2", { text: "Create container" }),
    !o.images.length ? el("p", { class: "error", text: "No local images. Download one first (see README)." }) : null,
    el("form", { class: "grid", onsubmit: e => { e.preventDefault(); run(async () => {
      const body = { name: name.value, image: image.value, pool: pool.value, network: network.value,
        ephemeral: ephemeral.checked, autostart: autostart.checked, description: description.value };
      for (const s of sliders) body[s.key] = s.read();
      if (owner) body.owner_id = Number(owner);
      notify(`Creating ${body.name}… this takes a few seconds.`);
      const r = await api("POST", "/api/containers", body);
      notify(`${r.name} created${r.started ? " and started" : ", but it did not start"}.`);
      refresh(); renderCreate(box, owner); renderUsers(document.getElementById("people"));
    }); } },
      el("label", {}, "Owner: ", ownerSelect),
      el("label", {}, "Name: ", name),
      el("label", {}, "Image: ", image),
      el("label", {}, "Storage pool: ", pool),
      el("label", {}, "Network: ", network),
      sliders.map(s => s.node),
      el("label", {}, ephemeral, "Ephemeral (deleted automatically when stopped)"),
      el("label", {}, autostart, "Start on boot"),
      el("label", {}, "Description: ", description),
      el("div", {}, el("button", { text: "Create" }))));
}

async function renderUsers(box) {
  if (!box) return;
  box.id = "people";
  let cap, list;
  try { [cap, list] = await Promise.all([api("GET", "/api/capacity"), api("GET", "/api/users")]); }
  catch (e) { box.replaceChildren(el("p", { class: "error", text: "Could not load users: " + e.message })); return; }
  const allocated = Object.fromEntries(cap.users.map(u => [u.id, u.allocated]));
  const nameOf = id => (containers.find(c => c.id === id) || {}).name || id.slice(0, 8);
  const reload = () => renderUsers(box);

  const rows = list.users.map(u => {
    const a = allocated[u.id] || { mem: 0, cpu: 0, disk: 0 };
    const isMe = u.id === me.id;
    const role = el("select", { onchange: e => run(async () => {
      await api("PATCH", `/api/users/${u.id}`, { role: e.target.value }); notify(`${u.email} is now ${e.target.value}.`); reload();
    }) }, ["user", "admin"].map(r => el("option", { value: r, text: r })));
    role.value = u.role;
    role.disabled = isMe;
    const grant = el("select", { onchange: e => run(async () => {
      if (!e.target.value) return;
      await api("PUT", `/api/users/${u.id}/containers/${e.target.value}`); notify("Access granted."); reload();
    }) }, el("option", { value: "", text: "grant access…" }),
      containers.filter(c => !u.containers.includes(c.id)).map(c => el("option", { value: c.id, text: c.name })));
    return el("tr", {},
      el("td", { text: u.email }),
      el("td", {}, role),
      el("td", { text: u.role === "admin" ? "—"
        : `RAM ${bytes(a.mem)} / ${bytes(u.quota_mem)} · CPU ${a.cpu} / ${u.quota_cpu} · disk ${bytes(a.disk)} / ${bytes(u.quota_disk)}` }),
      el("td", {}, u.containers.map(id => el("button", { class: "chip", title: "Remove access", text: nameOf(id) + " ✕",
        onclick: () => run(async () => { await api("DELETE", `/api/users/${u.id}/containers/${id}`); notify("Access removed."); reload(); }) })),
        u.role === "user" ? grant : null),
      el("td", {}, u.role === "user" ? el("button", { text: "Edit quota", onclick: () => run(async () => {
          const v = prompt("New quota as: RAM GiB, cores, disk GiB", `${u.quota_mem / GiB}, ${u.quota_cpu}, ${u.quota_disk / GiB}`);
          if (v === null) return;
          const [m, c, d] = v.split(",").map(x => Number(x.trim()));
          await api("PATCH", `/api/users/${u.id}`, { quota_mem: Math.round(m * GiB), quota_cpu: c, quota_disk: Math.round(d * GiB) });
          notify("Quota updated."); reload();
        }) }) : null,
        isMe ? null : el("button", { class: "danger", text: "Revoke", onclick: () => run(async () => {
          if (!confirm(`Revoke ${u.email}? They lose all access immediately.`)) return;
          await api("DELETE", `/api/users/${u.id}`); notify("User revoked."); reload();
        }) })));
  });

  const email = el("input", { type: "email", required: "", placeholder: "name@gmail.com" });
  const role = el("select", {}, ["user", "admin"].map(r => el("option", { value: r, text: r })));
  const qm = el("input", { type: "number", min: "0", step: "0.5", value: "2", style: "width:70px" });
  const qc = el("input", { type: "number", min: "0", value: "2", style: "width:60px" });
  const qd = el("input", { type: "number", min: "0", value: "10", style: "width:70px" });
  const h = cap.host;
  box.replaceChildren(
    el("h2", { text: "Server usage (allocated limits)" }),
    el("p", { text: `RAM ${bytes(cap.allocated.mem)} of ${bytes(h.mem)} (1 GiB kept for the host) · CPU ${cap.allocated.cpu} of ${h.cpu} cores · `
      + Object.entries(h.disk).map(([p, t]) => `disk on ${p}: ${bytes(cap.allocated.disk[p])} of ${bytes(t)}`).join(" · ") }),
    cap.unlimited.length ? el("p", { class: "error", text: "No RAM/CPU limit, so not counted: " + cap.unlimited.join(", ") }) : null,
    el("h2", { text: "Users" }),
    el("div", { class: "scroll" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Email", "Role", "Allocated / quota", "Containers", ""].map(x => el("th", { text: x })))),
      el("tbody", {}, rows))),
    el("h3", { text: "Invite a user" }),
    el("form", { class: "row", onsubmit: e => { e.preventDefault(); run(async () => {
      await api("POST", "/api/users", { email: email.value, role: role.value,
        quota_mem: Math.round(Number(qm.value) * GiB), quota_cpu: Number(qc.value), quota_disk: Math.round(Number(qd.value) * GiB) });
      notify(`Invited ${email.value}.`); reload(); renderCreate(extraBox.firstChild, "");
    }); } }, email, role, "RAM GiB", qm, "cores", qc, "disk GiB", qd, el("button", { text: "Invite" })));
}

// ---------- normal user: own quota ----------
async function renderQuota() {
  const box = el("section", { class: "card" });
  extraBox.replaceChildren(box);
  try {
    const q = await api("GET", "/api/quota");
    box.replaceChildren(el("h2", { text: "Your quota" }), el("p", { text:
      `RAM ${bytes(q.allocated.mem)} of ${bytes(q.quota.mem)} · CPU ${q.allocated.cpu} of ${q.quota.cpu} cores · disk ${bytes(q.allocated.disk)} of ${bytes(q.quota.disk)}` }));
  } catch (e) { box.textContent = "Could not load your quota: " + e.message; }
}

start();

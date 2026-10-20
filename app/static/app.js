/* ===========================================================
   Docker Manager — interfaccia web (vanilla JS, zero dipendenze)
   =========================================================== */
"use strict";

/* ------------------------------ stato ------------------------------ */

const state = {
  session: { auth_enabled: false, authenticated: false, user: null },
  meta: { timezone: "UTC", compose_cli: false, stats_enabled: true },
  docker: { connected: false },
  containers: [],
  categories: [],
  stacks: [],
  counts: { total: 0, running: 0, stopped: 0, paused: 0, unhealthy: 0 },
  schedules: [],
  hooks: [],
  activity: [],
  view: "containers",
  group: "none",
  search: "",
  stateFilter: "all",
  filter: null,            // {kind:'category'|'stack', ref:string}
  selection: new Set(),    // nomi container
  busy: new Set(),         // chiavi azione in corso
  hookBase: localStorage.getItem("dm-hook-base") || "internal",
  auto: true,
  timer: null,
  modalOpen: false,
};

const STATE_LABELS = {
  running: "In esecuzione", exited: "Fermo", created: "Creato", paused: "In pausa",
  restarting: "Riavvio", dead: "Morto", removing: "Rimozione", unknown: "Sconosciuto",
};
const ACTION_LABELS = {
  start: "Avvia", stop: "Ferma", restart: "Riavvia", pause: "Pausa",
  unpause: "Riprendi", kill: "Termina", up: "compose up", down: "compose down",
};
// tinte del design system di Portainer (blue/indigo/purple/pink/error/orange/
// warning/success/teal/gray, livello 7 o 500)
const PALETTE = ["#0ba5ec", "#6172f3", "#7a5af8", "#ee46bc", "#f04438",
                 "#ef6820", "#f79009", "#12b76a", "#15b79e", "#667085"];
const STACK_COLORS = { removed: "#f04438", compose: "#0ba5ec", plain: "#667085" };
const ICON_CHOICES = ["📦", "🗄️", "🌐", "🛠️", "🎬", "🎵", "🏠", "🔒", "📊", "🤖", "🧪", "💾", "📡", "🎮", "⚙️", "🔥"];

/* ------------------------------ utilità ------------------------------ */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function icon(name, cls = "ic") {
  return `<svg class="${cls}"><use href="#i-${name}"/></svg>`;
}

function fmtBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exp = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** exp;
  return `${value >= 100 || exp === 0 ? Math.round(value) : value.toFixed(1)} ${units[exp]}`;
}

function fmtDuration(seconds) {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}g ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${s}s`;
}

function fmtDT(iso, tz) {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("it-IT", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit",
      timeZone: tz || undefined,
    }).format(new Date(iso));
  } catch { return iso; }
}

function fmtRelative(iso) {
  if (!iso) return "—";
  const delta = (Date.now() - new Date(iso).getTime()) / 1000;
  if (Math.abs(delta) < 60) return "adesso";
  const future = delta < 0;
  const txt = fmtDuration(Math.abs(delta));
  return future ? `tra ${txt}` : `${txt} fa`;
}

function debounce(fn, ms) {
  let handle;
  return (...args) => { clearTimeout(handle); handle = setTimeout(() => fn(...args), ms); };
}

/* ------------------------------ API ------------------------------ */

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    state.session.authenticated = false;
    showLogin();
    throw new Error("sessione scaduta");
  }
  let payload = null;
  const text = await res.text();
  if (text) { try { payload = JSON.parse(text); } catch { payload = { detail: text }; } }
  if (!res.ok) {
    const detail = payload?.detail;
    throw new Error(typeof detail === "string" ? detail
      : Array.isArray(detail) ? detail.map((d) => d.msg || d).join(", ")
      : `errore ${res.status}`);
  }
  return payload;
}

/* ------------------------------ toast ------------------------------ */

const TOAST_ICONS = { ok: "check", err: "close", warn: "bolt", info: "pulse" };

function toast(kind, title, text = "", ttl = 4600) {
  const node = document.createElement("div");
  node.className = `toast toast-${kind}`;
  node.innerHTML = `<div class="toast-ic">${icon(TOAST_ICONS[kind] || "pulse")}</div>
    <div><div class="toast-title">${esc(title)}</div>${text ? `<div class="toast-text">${esc(text)}</div>` : ""}</div>`;
  $("#toasts").appendChild(node);
  setTimeout(() => {
    node.classList.add("leaving");
    setTimeout(() => node.remove(), 220);
  }, ttl);
}

function reportResult(result, fallbackTitle) {
  const rows = result?.results || [];
  const ok = rows.filter((r) => r.ok).length;
  const failed = rows.filter((r) => !r.ok);
  if (result?.status === "ok") {
    toast("ok", fallbackTitle, rows.length > 1 ? `${ok} container` : rows[0]?.message || "");
  } else if (result?.status === "partial") {
    toast("warn", `${fallbackTitle}: ${ok}/${rows.length} riusciti`,
      failed.map((f) => `${f.target}: ${f.message}`).join(" · "));
  } else {
    toast("err", `${fallbackTitle} non riuscita`,
      result?.message || failed.map((f) => `${f.target}: ${f.message}`).join(" · "));
  }
}

/* ------------------------------ tema ------------------------------ */

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("dm-theme", theme);
  $("#btn-theme").innerHTML = icon(theme === "dark" ? "moon" : "sun");
}

/* ------------------------------ caricamento dati ------------------------------ */

let refreshing = false;

async function refresh({ silent = true } = {}) {
  if (refreshing || !state.session.authenticated) return;
  refreshing = true;
  try {
    const overview = await api("/api/overview");
    Object.assign(state, {
      docker: overview.docker,
      containers: overview.containers,
      categories: overview.categories,
      stacks: overview.stacks,
      counts: overview.counts,
    });
    const names = new Set(state.containers.map((c) => c.name));
    state.selection = new Set([...state.selection].filter((n) => names.has(n)));

    if (state.view === "schedules" || !state.schedules.length) {
      state.schedules = await api("/api/schedules");
    }
    if (state.view === "hooks" || !state.hooks.length) {
      state.hooks = await api("/api/hooks");
    }
    if (state.view === "activity") {
      state.activity = await api("/api/activity?limit=120");
    }
    render();
  } catch (err) {
    if (!silent) toast("err", "Aggiornamento non riuscito", err.message);
  } finally {
    refreshing = false;
  }
}

function startAutoRefresh() {
  stopAutoRefresh();
  if (state.auto) state.timer = setInterval(() => { if (!state.modalOpen) refresh(); }, 5000);
}
function stopAutoRefresh() { if (state.timer) clearInterval(state.timer); state.timer = null; }

/* ------------------------------ render: guscio ------------------------------ */

function render() {
  renderDockerStatus();
  renderNavCounts();
  if (state.view === "containers") { renderStats(); renderFilters(); renderContainers(); }
  if (state.view === "categories") renderCategories();
  if (state.view === "stacks") renderStacks();
  if (state.view === "schedules") renderSchedules();
  if (state.view === "hooks") renderHooks();
  if (state.view === "activity") renderActivity();
  renderBulkbar();
}

function renderDockerStatus() {
  const node = $("#docker-status");
  const d = state.docker;
  if (d.connected) {
    node.className = "pill pill-ok";
    node.innerHTML = `<span class="dot"></span><span class="pill-label">Docker ${esc(d.docker_version)}</span>`;
    $("#host-info").innerHTML = `${esc(d.host)} · ${d.cpus} CPU · ${fmtBytes(d.memory)} RAM`;
  } else {
    node.className = "pill pill-err";
    node.innerHTML = `<span class="dot"></span><span class="pill-label">Docker non raggiungibile</span>`;
    node.title = d.error || "";
    $("#host-info").textContent = "daemon non raggiungibile";
  }
}

function renderNavCounts() {
  $("#nav-count-containers").textContent = state.counts.total || "";
  $("#nav-count-categories").textContent = state.categories.length || "";
  $("#nav-count-stacks").textContent = state.stacks.length || "";
  $("#nav-count-schedules").textContent = state.schedules.filter((s) => s.enabled).length || "";
  $("#nav-count-hooks").textContent = state.hooks.filter((h) => h.enabled).length || "";
}

function renderStats() {
  const c = state.counts;
  const tiles = [
    { v: c.total, l: "Container", ic: "box", color: "var(--accent)", soft: "var(--accent-soft)" },
    { v: c.running, l: "In esecuzione", ic: "play", color: "var(--ok)", soft: "var(--ok-soft)" },
    { v: c.stopped, l: "Fermi", ic: "stop", color: "var(--muted)", soft: "var(--surface-3)" },
    { v: c.paused + c.unhealthy, l: "Pausa / problemi", ic: "bolt", color: "var(--warn)", soft: "var(--warn-soft)" },
  ];
  $("#stats").innerHTML = tiles.map((t) => `
    <div class="stat">
      <div class="stat-ic" style="background:${t.soft};color:${t.color}">${icon(t.ic)}</div>
      <div><div class="stat-val">${t.v ?? 0}</div><div class="stat-lbl">${t.l}</div></div>
    </div>`).join("");
}

/* La barra dei filtri vive nella pagina Container: un solo posto dove
   restringere l'elenco, invece delle tre liste che c'erano nella sidebar. */
function renderFilters() {
  const states = [
    ["all", "Tutti", state.counts.total],
    ["running", "In esecuzione", state.counts.running],
    ["stopped", "Fermi", state.counts.stopped],
    ["paused", "In pausa", state.counts.paused],
  ];
  $("#state-filters").innerHTML = states.map(([value, label, count]) => `
    <button type="button" data-act="state-filter" data-value="${value}"
      class="${state.stateFilter === value ? "is-active" : ""}">${label}
      <span class="seg-count">${count ?? 0}</span></button>`).join("");

  const catRef = state.filter?.kind === "category" ? state.filter.ref : "";
  $("#filter-category").innerHTML = `<option value="">Tutte le categorie</option>` +
    state.categories.map((c) =>
      `<option value="${c.id}" ${String(c.id) === catRef ? "selected" : ""}>${esc(c.icon)} ${esc(c.name)} (${c.running}/${c.total})</option>`).join("");

  const stackRef = state.filter?.kind === "stack" ? state.filter.ref : "";
  $("#filter-stack").innerHTML = `<option value="">Tutti gli stack</option>` +
    state.stacks.map((s) =>
      `<option value="${esc(s.name)}" ${s.name === stackRef ? "selected" : ""}>${esc(s.name)}${s.removed ? " — rimosso" : ` (${s.running}/${s.total})`}</option>`).join("");

  $("#group-mode").value = state.group;
  $(".filter-reset").hidden = !(state.filter || state.stateFilter !== "all" || state.search);
}

/* ------------------------------ render: container ------------------------------ */

function visibleContainers() {
  const query = state.search.trim().toLowerCase();
  return state.containers.filter((c) => {
    if (state.stateFilter === "running" && c.state !== "running") return false;
    if (state.stateFilter === "paused" && c.state !== "paused") return false;
    if (state.stateFilter === "stopped" && !["exited", "created", "dead"].includes(c.state)) return false;
    if (state.filter?.kind === "stack" && (c.stack || "(senza stack)") !== state.filter.ref) return false;
    if (state.filter?.kind === "category" &&
        !c.categories.some((cat) => String(cat.id) === state.filter.ref)) return false;
    if (query) {
      const haystack = [c.name, c.image, c.stack, c.service, c.short_id,
        ...c.categories.map((cat) => cat.name)].join(" ").toLowerCase();
      if (!haystack.includes(query)) return false;
    }
    return true;
  });
}

function filterNote(shown) {
  const total = state.containers.length;
  if (shown === total) return `${total} in tutto`;
  return `${shown} di ${total} — filtrati`;
}

function renderContainers() {
  const root = $("#containers-root");
  const items = visibleContainers();
  $("#filter-note").textContent = filterNote(items.length);

  if (!state.docker.connected) {
    root.innerHTML = emptyState("bolt", "Docker non raggiungibile",
      esc(state.docker.error || "Verifica che /var/run/docker.sock sia montato nel container."));
    return;
  }
  // stack rimossi con "compose down": visibili nel raggruppamento per stack
  // e quando si filtra proprio su quello stack
  const removedPinned = state.filter?.kind === "stack"
    ? state.stacks.filter((s) => s.removed && s.name === state.filter.ref)
    : [];

  if (!items.length && state.group !== "stack" && !removedPinned.length) {
    root.innerHTML = emptyState("box", "Nessun container",
      state.containers.length ? "Nessun risultato per i filtri attivi." : "Non ci sono container su questo host.");
    return;
  }

  if (state.group === "none") {
    const panels = removedPinned.map((s) => removedStackSection({
      title: s.name, ref: s.name, color: STACK_COLORS.removed, canRecreate: s.can_recreate,
    })).join("");
    root.innerHTML = panels + `<div class="rows">${items.map(containerRow).join("")}</div>`;
    return;
  }

  const groups = [];
  if (state.group === "category") {
    for (const cat of state.categories) {
      const members = items.filter((c) => c.categories.some((x) => x.id === cat.id));
      if (members.length) groups.push({
        title: `${cat.icon} ${cat.name}`, color: cat.color, kind: "category",
        ref: String(cat.id), members,
      });
    }
    const orphans = items.filter((c) => !c.categories.length);
    if (orphans.length) groups.push({ title: "Senza categoria", color: STACK_COLORS.plain, kind: null, ref: null, members: orphans });
  } else {
    for (const stack of state.stacks) {
      const members = items.filter((c) => (c.stack || "(senza stack)") === stack.name);
      // gli stack rimossi con "compose down" restano visibili per poterli ricreare
      if (!members.length && !stack.removed) continue;
      groups.push({
        title: stack.name,
        color: stack.removed ? STACK_COLORS.removed : stack.has_compose ? STACK_COLORS.compose : STACK_COLORS.plain,
        kind: "stack", ref: stack.name, members, compose: stack.has_compose,
        removed: stack.removed, canRecreate: stack.can_recreate,
      });
    }
  }

  root.innerHTML = groups.map((g) => {
    const running = g.members.filter((c) => c.state === "running").length;
    if (g.removed) return removedStackSection(g);
    const composeBtns = g.compose && g.canRecreate ? `
      <button class="btn btn-sm btn-ghost" data-act="group-action" data-kind="stack" data-ref="${esc(g.ref)}" data-action="up">compose up</button>
      <button class="btn btn-sm btn-ghost" data-act="group-action" data-kind="stack" data-ref="${esc(g.ref)}" data-action="down">compose down</button>` : "";
    const acts = g.kind ? `
      <button class="btn btn-sm btn-ok" data-act="group-action" data-kind="${g.kind}" data-ref="${esc(g.ref)}" data-action="start">${icon("play")}Avvia</button>
      <button class="btn btn-sm btn-danger" data-act="group-action" data-kind="${g.kind}" data-ref="${esc(g.ref)}" data-action="stop">${icon("stop")}Ferma</button>
      <button class="btn btn-sm btn-ghost" data-act="group-action" data-kind="${g.kind}" data-ref="${esc(g.ref)}" data-action="restart">${icon("restart")}</button>
      ${composeBtns}
      <button class="btn btn-sm btn-ghost" data-act="schedule-for" data-kind="${g.kind}" data-ref="${esc(g.ref)}">${icon("clock")}Pianifica</button>
      <button class="btn btn-sm btn-ghost" data-act="select-group" data-names="${esc(g.members.map((m) => m.name).join(","))}">${icon("check")}</button>` : "";
    return `<section>
      <div class="group-head">
        <div class="group-title"><span class="swatch" style="background:${esc(g.color)}"></span>${esc(g.title)}</div>
        <div class="group-meta">${running}/${g.members.length} attivi</div>
        <div class="group-acts">${acts}</div>
      </div>
      <div class="rows">${g.members.map(containerRow).join("")}</div>
    </section>`;
  }).join("");
}

function removedStackSection({ title, ref, color, canRecreate }) {
  return `<section>
    <div class="group-head">
      <div class="group-title"><span class="swatch" style="background:${esc(color || STACK_COLORS.removed)}"></span>${esc(title)}</div>
      <div class="group-meta">rimosso</div>
      <div class="group-acts">
        ${canRecreate
          ? `<button class="btn btn-sm btn-ok" data-act="group-action" data-kind="stack" data-ref="${esc(ref)}" data-action="up">${icon("bolt")}compose up</button>`
          : ""}
        <button class="btn btn-sm btn-danger" data-act="forget-stack" data-ref="${esc(ref)}">${icon("trash")}Dimentica</button>
      </div>
    </div>
    <div class="panel"><div class="empty" style="padding:22px">
      <div>Nessun container: lo stack è stato rimosso con <code>compose down</code>.</div>
      <div class="muted">${canRecreate
        ? "Usa <b>compose up</b> per ricrearlo dal file compose."
        : "Per ricrearlo serve <code>COMPOSE_CLI=true</code> e la cartella del progetto montata nel container."}</div>
    </div></div>
  </section>`;
}

function emptyState(ic, title, text) {
  return `<div class="panel"><div class="empty">${icon(ic)}<h3>${title}</h3><div>${text}</div></div></div>`;
}

function containerRow(c) {
  const selected = state.selection.has(c.name);
  const running = c.state === "running";
  const paused = c.state === "paused";
  const stats = c.stats || {};
  const health = c.health
    ? `<span class="badge" style="color:${c.health === "healthy" ? "var(--ok)" : c.health === "unhealthy" ? "var(--err)" : "var(--warn)"}">${esc(c.health)}</span>`
    : "";
  const cats = c.categories.map((cat) =>
    `<span class="badge" title="categoria ${esc(cat.name)}"><span class="swatch" style="background:${esc(cat.color)}"></span>${esc(cat.name)}</span>`).join("");
  const stack = c.stack
    ? `<span class="badge" title="stack compose">${icon("layers", "ic")}${esc(c.stack)}</span>`.replace('class="ic"', 'class="ic" style="width:11px;height:11px"')
    : "";
  const links = c.ports.filter((p) => p.url).slice(0, 2).map((p) =>
    `<a class="badge badge-link" href="${esc(p.url)}" target="_blank" rel="noopener" title="${esc(p.container)} → ${esc(p.host)}">${icon("link")}${esc(p.url.replace(/^https?:\/\//, ""))}</a>`
  ).join("").replace(/class="ic"/g, 'class="ic" style="width:11px;height:11px"');

  const meters = state.meta.stats_enabled && running ? `
    <div class="meters">
      <div class="meter-row"><b>CPU</b>
        <div class="meter ${stats.cpu_percent > 80 ? "hot" : ""}"><span style="width:${Math.min(100, stats.cpu_percent || 0)}%"></span></div>
        <span class="meter-val">${(stats.cpu_percent ?? 0).toFixed(1)}%</span></div>
      <div class="meter-row"><b>RAM</b>
        <div class="meter ${stats.mem_percent > 85 ? "hot" : ""}"><span style="width:${Math.min(100, stats.mem_percent || 0)}%"></span></div>
        <span class="meter-val">${fmtBytes(stats.mem_usage || 0)}</span></div>
    </div>` : `<div class="meters"></div>`;

  const busyKey = `c:${c.name}`;
  const isBusy = state.busy.has(busyKey);
  const act = (action, cls, title, ic, enabled) =>
    `<button class="act ${cls} ${isBusy ? "busy" : ""}" title="${title}" ${enabled && !isBusy ? "" : "disabled"}
      data-act="container-action" data-name="${esc(c.name)}" data-action="${action}">${icon(ic)}</button>`;

  return `<div class="crow ${selected ? "is-selected" : ""} ${running ? "" : "is-down"} ${c.is_self ? "is-self" : ""}"
    data-name="${esc(c.name)}" data-state="${esc(c.state)}">
    <input class="cbox" type="checkbox" data-act="select" data-name="${esc(c.name)}" ${selected ? "checked" : ""} aria-label="seleziona ${esc(c.name)}">
    <div class="cmain">
      <div class="cname-row">
        <span class="state-dot state-${esc(c.state)}" title="${esc(STATE_LABELS[c.state] || c.state)}"></span>
        <button class="cname cname-link" data-act="open-container" data-name="${esc(c.name)}" title="Dettagli di ${esc(c.name)}">${esc(c.name)}</button>
        ${c.is_self ? `<span class="badge" style="color:var(--accent-text)">questo pannello</span>` : ""}
      </div>
      <div class="cmeta"><span class="cimage" title="${esc(c.image)}">${esc(c.image)}</span></div>
    </div>
    <div class="cbadges cimage-cell">${stack}${cats}${health}${links}</div>
    <div class="cuptime-cell">
      <div class="cstate" style="color:${running ? "var(--ok)" : paused ? "var(--warn)" : "var(--muted)"}">${esc(STATE_LABELS[c.state] || c.state)}</div>
      <div class="cuptime">${running ? `attivo ${fmtDuration(c.uptime_seconds)}` : c.finished_at ? `da ${fmtRelative(c.finished_at)}` : "—"}</div>
    </div>
    ${meters}
    <div class="cacts">
      ${running || paused ? "" : act("start", "go", "Avvia", "play", true)}
      ${paused ? act("unpause", "go", "Riprendi", "play", true) : ""}
      ${running ? act("pause", "warn", "Metti in pausa", "pause", true) : ""}
      ${running || paused ? act("stop", "halt", "Ferma", "stop", true) : ""}
      ${running ? act("restart", "", "Riavvia", "restart", true) : ""}
      <button class="act" title="Log" data-act="open-logs" data-name="${esc(c.name)}">${icon("logs")}</button>
      <button class="act" title="Categorie" data-act="assign" data-name="${esc(c.name)}">${icon("tag")}</button>
      <button class="act" title="Pianifica" data-act="schedule-for" data-kind="containers" data-ref="${esc(c.name)}">${icon("clock")}</button>
    </div>
  </div>`;
}

/* ------------------------------ render: barra di massa ------------------------------ */

function renderBulkbar() {
  const bar = $("#bulkbar");
  const count = state.selection.size;
  if (!count || state.view !== "containers") { bar.hidden = true; return; }
  bar.hidden = false;
  bar.innerHTML = `
    <div class="bulk-count"><b>${count}</b> selezionati</div>
    <div class="bulk-sep"></div>
    <button class="btn btn-sm btn-ok" data-act="bulk" data-action="start">${icon("play")}Avvia</button>
    <button class="btn btn-sm btn-danger" data-act="bulk" data-action="stop">${icon("stop")}Ferma</button>
    <button class="btn btn-sm" data-act="bulk" data-action="restart">${icon("restart")}Riavvia</button>
    <button class="btn btn-sm btn-warn" data-act="bulk" data-action="pause">${icon("pause")}Pausa</button>
    <div class="bulk-sep"></div>
    <button class="btn btn-sm btn-primary" data-act="schedule-for" data-kind="selection">${icon("clock")}Pianifica</button>
    <button class="btn btn-sm" data-act="bulk-assign">${icon("tag")}Categoria</button>
    <button class="btn btn-sm btn-ghost" data-act="clear-selection">${icon("close")}</button>`;
}

/* ------------------------------ render: categorie ------------------------------ */

function renderCategories() {
  const root = $("#categories-root");
  if (!state.categories.length) {
    root.innerHTML = emptyState("tag", "Nessuna categoria",
      "Le categorie servono per avviare o spegnere gruppi di container con un clic.");
    return;
  }
  root.innerHTML = state.categories.map((cat) => {
    const pct = cat.total ? Math.round((cat.running / cat.total) * 100) : 0;
    const members = cat.containers.map((name) => {
      const c = state.containers.find((x) => x.name === name);
      const color = !c ? "var(--muted)" : c.state === "running" ? "var(--ok)" : c.state === "paused" ? "var(--warn)" : "var(--muted)";
      return `<span class="badge" title="${c ? esc(STATE_LABELS[c.state]) : "container non presente"}">
        <span class="swatch" style="background:${color}"></span>${esc(name)}${c ? "" : " ⚠︎"}</span>`;
    }).join("") || `<span class="muted">Nessun container assegnato</span>`;

    return `<article class="card">
      <div class="card-head">
        <div class="card-ic" style="background:${esc(cat.color)}22;color:${esc(cat.color)}">${esc(cat.icon)}</div>
        <div style="min-width:0">
          <div class="card-title">${esc(cat.name)}</div>
          <div class="card-sub">${cat.running}/${cat.total} attivi${cat.description ? ` · ${esc(cat.description)}` : ""}</div>
        </div>
        <div class="card-head-acts">
          <button class="act" title="Modifica" data-act="edit-category" data-id="${cat.id}">${icon("edit")}</button>
          <button class="act halt" title="Elimina" data-act="delete-category" data-id="${cat.id}">${icon("trash")}</button>
        </div>
      </div>
      <div class="card-body">
        <div class="progress"><span style="width:${pct}%"></span></div>
        <div class="member-list">${members}</div>
      </div>
      <div class="card-foot">
        <button class="btn btn-sm btn-ok" data-act="group-action" data-kind="category" data-ref="${cat.id}" data-action="start">${icon("play")}Avvia tutti</button>
        <button class="btn btn-sm btn-danger" data-act="group-action" data-kind="category" data-ref="${cat.id}" data-action="stop">${icon("stop")}Ferma tutti</button>
        <button class="btn btn-sm btn-ghost" data-act="group-action" data-kind="category" data-ref="${cat.id}" data-action="restart">${icon("restart")}Riavvia</button>
        <button class="btn btn-sm btn-ghost" data-act="schedule-for" data-kind="category" data-ref="${cat.id}">${icon("clock")}Pianifica</button>
      </div>
    </article>`;
  }).join("");
}

/* ------------------------------ render: stack ------------------------------ */

function renderStacks() {
  const root = $("#stacks-root");
  if (!state.stacks.length) {
    root.innerHTML = emptyState("layers", "Nessuno stack",
      "Gli stack sono i progetti <code>docker compose</code> presenti sull'host: compaiono qui da soli.");
    return;
  }
  root.innerHTML = state.stacks.map((s) => {
    const color = s.removed ? STACK_COLORS.removed : s.has_compose ? STACK_COLORS.compose : STACK_COLORS.plain;
    const pct = s.total ? Math.round((s.running / s.total) * 100) : 0;
    const members = state.containers
      .filter((c) => (c.stack || "(senza stack)") === s.name)
      .map((c) => `<span class="badge" title="${esc(STATE_LABELS[c.state] || c.state)}">
        <span class="swatch state-${esc(c.state)}"></span>${esc(c.name)}</span>`).join("")
      || `<span class="muted">Nessun container presente</span>`;

    const sub = s.removed
      ? "rimosso con compose down"
      : `${s.running}/${s.total} attivi${s.has_compose ? " · file compose disponibile" : ""}`;

    const composeBtns = s.has_compose && s.can_recreate ? `
      <button class="btn btn-sm btn-ghost" data-act="group-action" data-kind="stack" data-ref="${esc(s.name)}" data-action="up">${icon("bolt")}compose up</button>
      <button class="btn btn-sm btn-ghost" data-act="group-action" data-kind="stack" data-ref="${esc(s.name)}" data-action="down">${icon("stop")}compose down</button>` : "";

    const foot = s.removed
      ? `${s.can_recreate
            ? `<button class="btn btn-sm btn-ok" data-act="group-action" data-kind="stack" data-ref="${esc(s.name)}" data-action="up">${icon("bolt")}Ricrea</button>`
            : ""}
         <button class="btn btn-sm btn-danger" data-act="forget-stack" data-ref="${esc(s.name)}">${icon("trash")}Dimentica</button>`
      : `<button class="btn btn-sm btn-ok" data-act="group-action" data-kind="stack" data-ref="${esc(s.name)}" data-action="start">${icon("play")}Avvia tutti</button>
         <button class="btn btn-sm btn-danger" data-act="group-action" data-kind="stack" data-ref="${esc(s.name)}" data-action="stop">${icon("stop")}Ferma tutti</button>
         <button class="btn btn-sm btn-ghost" data-act="group-action" data-kind="stack" data-ref="${esc(s.name)}" data-action="restart">${icon("restart")}Riavvia</button>
         ${composeBtns}
         <button class="btn btn-sm btn-ghost" data-act="schedule-for" data-kind="stack" data-ref="${esc(s.name)}">${icon("clock")}Pianifica</button>
         <button class="btn btn-sm btn-ghost" data-act="show-containers" data-kind="stack" data-ref="${esc(s.name)}">${icon("box")}Mostra</button>`;

    return `<article class="card">
      <div class="card-head">
        <div class="card-ic" style="background:${esc(color)}22;color:${esc(color)}">${icon("layers")}</div>
        <div style="min-width:0">
          <div class="card-title">${esc(s.name)}</div>
          <div class="card-sub">${esc(sub)}</div>
        </div>
      </div>
      <div class="card-body">
        ${s.removed ? "" : `<div class="progress"><span style="width:${pct}%"></span></div>`}
        <div class="member-list">${members}</div>
        ${s.removed && !s.can_recreate
          ? `<div class="field-hint">Per ricrearlo serve <code>COMPOSE_CLI=true</code> e la cartella del progetto montata nel pannello.</div>`
          : ""}
      </div>
      <div class="card-foot">${foot}</div>
    </article>`;
  }).join("");
}

/* ------------------------------ render: hook di backup ------------------------------ */

/* Base degli URL mostrati nelle card.

   L'interfaccia conosce solo l'indirizzo da cui la stai navigando: dietro a un
   reverse proxy è quello pubblico, che però non è l'indirizzo giusto se il
   backup tool vive sulla stessa rete docker del pannello. Il server ci dice
   qual è l'indirizzo interno e qui si può scegliere quale mostrare. */
function hookBases() {
  const internal = state.meta.internal_base_url || "";
  const list = [];
  if (internal) list.push({ key: "internal", label: "Rete docker", url: internal });
  list.push({ key: "external", label: "Questo indirizzo", url: location.origin });
  return list;
}

function hookOrigin() {
  const bases = hookBases();
  const chosen = bases.find((b) => b.key === state.hookBase);
  return (chosen || bases[0]).url;
}

function renderHookBasePicker() {
  const host = $("#hook-base");
  const bases = hookBases();
  if (bases.length < 2) { host.hidden = true; return; }
  host.hidden = false;
  const current = hookOrigin();
  host.innerHTML = `
    <div class="filter-field">
      <span class="filter-label">URL da mostrare</span>
      <div class="segmented">${bases.map((b) =>
        `<button type="button" data-act="hook-base" data-value="${b.key}"
          class="${b.url === current ? "is-active" : ""}" title="${esc(b.url)}">${b.label}</button>`).join("")}</div>
    </div>
    <p class="muted" style="margin:0;flex:1;min-width:220px">
      ${state.hookBase === "external"
        ? `Indirizzo pubblico: usalo solo se il backup tool sta <b>fuori</b> dall'host. Passerà dal reverse proxy, che devi configurare per non chiedere autenticazione su <code>/hooks/</code>, e il token viaggerà su internet.`
        : `Indirizzo interno alla rete docker: è quello giusto se il backup tool gira su questo host. Non passa dal reverse proxy e il token non esce dalla rete. I due container devono condividere una rete.`}
    </p>`;
}

function renderHooks() {
  renderHookBasePicker();
  const root = $("#hooks-root");
  if (!state.hooks.length) {
    root.innerHTML = emptyState("hook", "Nessun hook di backup",
      `Un hook espone due URL da incollare nel tuo strumento di backup: il primo spegne
       i container, il secondo li riaccende a backup finito.`);
    return;
  }
  root.innerHTML = state.hooks.map((h) => {
    const sospesi = h.suspended.length;
    const tone = sospesi ? "var(--warn)" : h.enabled ? "var(--ok)" : "var(--muted)";
    const statusPill = sospesi
      ? `<span class="pill pill-warn"><span class="dot"></span>${sospesi} container sospesi</span>`
      : h.last_status
        ? `<span class="pill ${h.last_status === "ok" ? "pill-ok" : h.last_status === "partial" ? "pill-warn" : "pill-err"}"><span class="dot"></span>${esc(h.last_phase || h.last_status)}</span>`
        : `<span class="pill pill-muted"><span class="dot"></span>mai chiamato</span>`;

    const url = (path) => `${hookOrigin()}${path}`;
    const urlRow = (label, path) => `
      <div class="url-row">
        <span class="url-tag">${label}</span>
        <input class="url-input mono" readonly value="${esc(url(path))}" onfocus="this.select()">
        <button class="act" title="Copia" data-act="copy" data-value="${esc(url(path))}">${icon("copy")}</button>
      </div>`;

    return `<article class="card" style="${h.enabled ? "" : "opacity:.62"}">
      <div class="card-head">
        <div class="card-ic" style="background:color-mix(in srgb, ${tone} 16%, transparent);color:${tone}">${icon("hook")}</div>
        <div style="min-width:0">
          <div class="card-title">${esc(h.name)}</div>
          <div class="card-sub">${h.mode === "pause" ? "Mette in pausa" : "Spegne"} → ${esc(h.target_label)}</div>
        </div>
        <div class="card-head-acts">
          <label class="switch" title="${h.enabled ? "Disattiva" : "Attiva"}">
            <input type="checkbox" data-act="toggle-hook" data-id="${h.id}" ${h.enabled ? "checked" : ""}>
            <span class="switch-track"></span></label>
        </div>
      </div>
      <div class="card-body">
        ${urlRow("pre", h.pre_url)}
        ${urlRow("post", h.post_url)}
        ${sospesi ? `<div class="form-ok" style="background:var(--warn-soft);color:var(--warn);border-color:color-mix(in srgb, var(--warn) 28%, transparent)">
            Sospesi da ${fmtRelative(h.suspended_at)}: <b>${h.suspended.map(esc).join(", ")}</b>.
            ${h.resume_after_minutes
              ? `Se il post non arriva, li riaccendo da solo dopo ${h.resume_after_minutes} minuti.`
              : "La ripresa automatica è disattivata: riprendili a mano se il backup si è piantato."}
          </div>` : ""}
        <dl class="kv">
          <dt>Ripresa</dt><dd>${h.resume_after_minutes ? `automatica dopo ${h.resume_after_minutes} min senza post` : "<span class=\"muted\">solo su chiamata del post</span>"}</dd>
          <dt>Ultima</dt><dd>${h.last_run_at ? `${fmtDT(h.last_run_at, state.meta.timezone)} — ${esc(h.last_message || "")}` : "<span class=\"muted\">—</span>"}</dd>
        </dl>
        <div>${statusPill} <span class="muted">${h.run_count} chiamate</span></div>
      </div>
      <div class="card-foot">
        ${sospesi
          ? `<button class="btn btn-sm btn-ok" data-act="hook-run" data-id="${h.id}" data-phase="post">${icon("play")}Riprendi adesso</button>`
          : `<button class="btn btn-sm" data-act="hook-run" data-id="${h.id}" data-phase="pre">${icon("bolt")}Prova il pre</button>`}
        <button class="btn btn-sm btn-ghost" data-act="edit-hook" data-id="${h.id}">${icon("edit")}Modifica</button>
        <button class="btn btn-sm btn-ghost" data-act="rotate-hook" data-id="${h.id}">${icon("key")}Rigenera token</button>
        <button class="btn btn-sm btn-danger" data-act="delete-hook" data-id="${h.id}">${icon("trash")}Elimina</button>
      </div>
    </article>`;
  }).join("");
}

/* ------------------------------ render: pianificazioni ------------------------------ */

const WEEKDAY_NAMES = ["dom", "lun", "mar", "mer", "gio", "ven", "sab"];

function describeTrigger(s) {
  if (s.trigger_kind === "interval") {
    const m = s.interval_minutes;
    if (m % 1440 === 0) return `ogni ${m / 1440} giorni`;
    if (m % 60 === 0) return `ogni ${m / 60} ore`;
    return `ogni ${m} minuti`;
  }
  if (s.trigger_kind === "once") return `una volta — ${fmtDT(s.run_at, s.timezone_name)}`;
  const parsed = parseCron(s.cron_expr);
  if (parsed.mode === "daily") return `ogni giorno alle ${parsed.time}`;
  if (parsed.mode === "weekly") {
    const days = parsed.weekdays.map((d) => WEEKDAY_NAMES[Number(d)]).join(", ");
    return `${days} alle ${parsed.time}`;
  }
  if (parsed.mode === "monthly") return `il giorno ${parsed.day} alle ${parsed.time}`;
  return `cron: ${s.cron_expr}`;
}

function renderSchedules() {
  $("#tz-note").textContent = `Fuso orario: ${state.meta.timezone}`;
  const root = $("#schedules-root");
  if (!state.schedules.length) {
    root.innerHTML = emptyState("clock", "Nessuna pianificazione",
      "Programma accensioni e spegnimenti ricorrenti o una-tantum per container, categorie e stack.");
    return;
  }
  root.innerHTML = state.schedules.map((s) => {
    const tone = s.action === "stop" || s.action === "down" ? "var(--err)"
      : s.action === "start" || s.action === "up" ? "var(--ok)" : "var(--warn)";
    const statusPill = s.last_status
      ? `<span class="pill ${s.last_status === "ok" ? "pill-ok" : s.last_status === "partial" ? "pill-warn" : "pill-err"}"><span class="dot"></span>${s.last_status}</span>`
      : `<span class="pill pill-muted"><span class="dot"></span>mai eseguita</span>`;
    return `<article class="card" style="${s.enabled ? "" : "opacity:.62"}">
      <div class="card-head">
        <div class="card-ic" style="background:color-mix(in srgb, ${tone} 16%, transparent);color:${tone}">${icon("clock")}</div>
        <div style="min-width:0">
          <div class="card-title">${esc(s.name)}</div>
          <div class="card-sub"><b style="color:${tone}">${esc(ACTION_LABELS[s.action] || s.action)}</b> → ${esc(s.target_label)}</div>
        </div>
        <div class="card-head-acts">
          <label class="switch" title="${s.enabled ? "Disattiva" : "Attiva"}">
            <input type="checkbox" data-act="toggle-schedule" data-id="${s.id}" ${s.enabled ? "checked" : ""}>
            <span class="switch-track"></span></label>
        </div>
      </div>
      <div class="card-body">
        <dl class="kv">
          <dt>Quando</dt><dd>${esc(describeTrigger(s))}</dd>
          <dt>Prossima</dt><dd>${s.next_run ? `${fmtDT(s.next_run, s.timezone_name)} <span class="muted">(${fmtRelative(s.next_run)})</span>` : "<span class=\"muted\">non programmata</span>"}</dd>
          <dt>Ultima</dt><dd>${s.last_run_at ? `${fmtDT(s.last_run_at, s.timezone_name)} — ${esc(s.last_message || "")}` : "<span class=\"muted\">—</span>"}</dd>
        </dl>
        ${s.next_runs.length > 1 ? `<div class="muted">poi: ${s.next_runs.slice(1, 3).map((d) => fmtDT(d, s.timezone_name)).join(" · ")}</div>` : ""}
        <div>${statusPill} <span class="muted">${s.run_count} esecuzioni</span></div>
      </div>
      <div class="card-foot">
        <button class="btn btn-sm" data-act="run-schedule" data-id="${s.id}">${icon("bolt")}Esegui ora</button>
        <button class="btn btn-sm btn-ghost" data-act="edit-schedule" data-id="${s.id}">${icon("edit")}Modifica</button>
        <button class="btn btn-sm btn-danger" data-act="delete-schedule" data-id="${s.id}">${icon("trash")}Elimina</button>
      </div>
    </article>`;
  }).join("");
}

/* ------------------------------ render: attività ------------------------------ */

function renderActivity() {
  const root = $("#activity-root");
  if (!state.activity.length) {
    root.innerHTML = `<div class="empty">${icon("pulse")}<h3>Nessuna attività</h3><div>Le azioni eseguite compariranno qui.</div></div>`;
    return;
  }
  root.innerHTML = `<div class="table-wrap"><table class="table">
    <thead><tr><th>Quando</th><th>Origine</th><th>Azione</th><th>Destinazione</th><th>Esito</th><th>Dettaglio</th></tr></thead>
    <tbody>${state.activity.map((a) => `
      <tr>
        <td style="white-space:nowrap">${fmtDT(a.created_at, state.meta.timezone)}<div class="muted">${fmtRelative(a.created_at)}</div></td>
        <td><span class="badge">${a.source === "schedule" ? "pianificata" : "manuale"}</span></td>
        <td><b>${esc(ACTION_LABELS[a.action] || a.action)}</b></td>
        <td>${esc(a.target)}</td>
        <td><span class="pill ${a.status === "ok" ? "pill-ok" : a.status === "partial" ? "pill-warn" : "pill-err"}"><span class="dot"></span>${esc(a.status)}</span></td>
        <td class="muted">${esc(a.message)}</td>
      </tr>`).join("")}</tbody></table></div>`;
}

/* ------------------------------ azioni ------------------------------ */

async function doAction({ action, target_kind = "containers", target_ref = "", targets = [], label, busyKey }) {
  if (busyKey) { state.busy.add(busyKey); render(); }
  try {
    const result = await api("/api/actions", {
      method: "POST", body: { action, target_kind, target_ref, targets },
    });
    reportResult(result, label || ACTION_LABELS[action] || action);
  } catch (err) {
    toast("err", "Operazione non riuscita", err.message);
  } finally {
    if (busyKey) state.busy.delete(busyKey);
    await refresh();
  }
}

/* ------------------------------ modali: infrastruttura ------------------------------ */

function openModal({ title, subtitle = "", body, footer = "", wide = false, onMount }) {
  closeModal();
  state.modalOpen = true;
  const overlay = document.createElement("div");
  overlay.className = "overlay";
  overlay.id = "overlay";
  overlay.innerHTML = `<div class="modal ${wide ? "modal-wide" : ""}" role="dialog" aria-modal="true">
    <div class="modal-head">
      <div style="min-width:0"><h3>${title}</h3>${subtitle ? `<span class="muted">${subtitle}</span>` : ""}</div>
      <button class="icon-btn" style="margin-left:auto" data-act="close-modal" title="Chiudi">${icon("close")}</button>
    </div>
    <div class="modal-body">${body}</div>
    ${footer ? `<div class="modal-foot">${footer}</div>` : ""}
  </div>`;
  overlay.addEventListener("mousedown", (ev) => { if (ev.target === overlay) closeModal(); });
  $("#modal-root").appendChild(overlay);
  if (onMount) onMount(overlay);
  const firstField = overlay.querySelector("input:not([type=checkbox]), select, textarea");
  if (firstField) firstField.focus();
  return overlay;
}

function closeModal() {
  $("#modal-root").innerHTML = "";
  state.modalOpen = false;
}

/* ------------------------------ modale: dettagli container ------------------------------ */

async function openContainerModal(name) {
  const c = state.containers.find((x) => x.name === name);
  if (!c) return;
  const ports = c.ports.length
    ? c.ports.map((p) => `<div>${esc(p.container)} ${p.host ? `→ <span class="mono">${esc(p.host)}</span>` : "<span class=\"muted\">non pubblicata</span>"}${p.url ? ` <a href="${esc(p.url)}" target="_blank" rel="noopener">apri</a>` : ""}</div>`).join("")
    : `<span class="muted">nessuna porta</span>`;

  openModal({
    title: esc(c.name),
    subtitle: `${esc(c.image)} · ${esc(c.short_id)}`,
    wide: true,
    body: `
      <div class="row2">
        <dl class="kv">
          <dt>Stato</dt><dd>${esc(STATE_LABELS[c.state] || c.state)}${c.health ? ` · ${esc(c.health)}` : ""}</dd>
          <dt>Uptime</dt><dd>${c.state === "running" ? fmtDuration(c.uptime_seconds) : "—"}</dd>
          <dt>Avviato</dt><dd>${fmtDT(c.started_at, state.meta.timezone)}</dd>
          <dt>Creato</dt><dd>${fmtDT(c.created_at, state.meta.timezone)}</dd>
          <dt>Riavvio</dt><dd>${esc(c.restart_policy)}</dd>
        </dl>
        <dl class="kv">
          <dt>Stack</dt><dd>${c.stack ? esc(c.stack) : "<span class=\"muted\">—</span>"}${c.service ? ` / ${esc(c.service)}` : ""}</dd>
          <dt>Categorie</dt><dd>${c.categories.map((x) => esc(x.name)).join(", ") || "<span class=\"muted\">—</span>"}</dd>
          <dt>Reti</dt><dd>${c.networks.map(esc).join(", ") || "—"}</dd>
          <dt>Porte</dt><dd>${ports}</dd>
          <dt>CPU / RAM</dt><dd>${c.stats?.cpu_percent != null ? `${c.stats.cpu_percent.toFixed(1)}% · ${fmtBytes(c.stats.mem_usage)}` : "—"}</dd>
        </dl>
      </div>
      <div>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
          <b>Log</b>
          <select id="log-tail" class="btn btn-sm" style="width:auto">
            <option value="100">ultime 100 righe</option>
            <option value="300" selected>ultime 300</option>
            <option value="1000">ultime 1000</option>
          </select>
          <button class="btn btn-sm" id="log-reload">${icon("refresh")}Ricarica</button>
        </div>
        <div class="logbox" id="logbox">caricamento…</div>
      </div>`,
    footer: `
      <button class="btn btn-ok" data-act="container-action" data-name="${esc(c.name)}" data-action="start">${icon("play")}Avvia</button>
      <button class="btn btn-danger" data-act="container-action" data-name="${esc(c.name)}" data-action="stop">${icon("stop")}Ferma</button>
      <button class="btn" data-act="container-action" data-name="${esc(c.name)}" data-action="restart">${icon("restart")}Riavvia</button>
      <button class="btn btn-ghost" data-act="close-modal">Chiudi</button>`,
    onMount(overlay) {
      const load = async () => {
        const box = $("#logbox", overlay);
        const tail = $("#log-tail", overlay).value;
        box.textContent = "caricamento…";
        try {
          const data = await api(`/api/containers/${encodeURIComponent(name)}/logs?tail=${tail}`);
          box.textContent = data.logs?.trimEnd() || "(nessun log)";
          box.scrollTop = box.scrollHeight;
        } catch (err) { box.textContent = `errore: ${err.message}`; }
      };
      $("#log-reload", overlay).addEventListener("click", load);
      $("#log-tail", overlay).addEventListener("change", load);
      load();
    },
  });
}

async function openLogsModal(name) { openContainerModal(name); }

/* ------------------------------ modale: categoria ------------------------------ */

function openCategoryModal(categoryId = null) {
  const cat = categoryId ? state.categories.find((c) => c.id === categoryId) : null;
  const selected = new Set(cat ? cat.containers : [...state.selection]);
  const color = cat?.color || PALETTE[state.categories.length % PALETTE.length];

  openModal({
    title: cat ? `Modifica “${esc(cat.name)}”` : "Nuova categoria",
    subtitle: "Raggruppa i container per gestirli in blocco",
    body: `
      <div class="row2">
        <label class="field"><span>Nome</span><input id="cat-name" value="${esc(cat?.name || "")}" placeholder="es. Media, Database, Monitoring" maxlength="80"></label>
        <label class="field"><span>Icona</span>
          <select id="cat-icon">${ICON_CHOICES.map((emoji) =>
            `<option value="${emoji}" ${cat?.icon === emoji ? "selected" : ""}>${emoji}</option>`).join("")}</select></label>
      </div>
      <label class="field"><span>Descrizione</span><input id="cat-desc" value="${esc(cat?.description || "")}" placeholder="facoltativa"></label>
      <div class="field"><span>Colore</span>
        <div class="swatches" id="cat-colors">${PALETTE.map((hex) =>
          `<button type="button" class="sw ${hex === color ? "is-active" : ""}" data-color="${hex}" style="background:${hex}"></button>`).join("")}</div>
        <input type="hidden" id="cat-color" value="${esc(color)}">
      </div>
      <div class="field">
        <span>Container (${state.containers.length} disponibili)</span>
        <input id="cat-search" placeholder="filtra per nome…" autocomplete="off">
        <div class="picker" id="cat-picker"></div>
        <div class="field-hint">L'associazione è per <b>nome</b> del container: resta valida anche se lo ricrei.</div>
      </div>
      <p class="form-error" id="cat-error" hidden></p>`,
    footer: `${cat ? `<button class="btn btn-danger" data-act="delete-category" data-id="${cat.id}">${icon("trash")}Elimina</button>` : ""}
      <button class="btn btn-ghost" data-act="close-modal">Annulla</button>
      <button class="btn btn-primary" id="cat-save">${icon("check")}${cat ? "Salva" : "Crea categoria"}</button>`,
    onMount(overlay) {
      const picker = $("#cat-picker", overlay);
      const drawPicker = (query = "") => {
        const q = query.toLowerCase();
        const items = state.containers.filter((c) =>
          !q || c.name.toLowerCase().includes(q) || (c.image || "").toLowerCase().includes(q));
        picker.innerHTML = items.map((c) => `
          <label class="picker-item">
            <input type="checkbox" value="${esc(c.name)}" ${selected.has(c.name) ? "checked" : ""}>
            <span class="state-dot state-${esc(c.state)}"></span>
            <span>${esc(c.name)}</span>
            <span class="pi-sub">${esc(c.stack || "")}</span>
          </label>`).join("") || `<div class="muted" style="padding:10px">nessun risultato</div>`;
      };
      drawPicker();
      picker.addEventListener("change", (ev) => {
        const box = ev.target;
        if (box.type !== "checkbox") return;
        box.checked ? selected.add(box.value) : selected.delete(box.value);
      });
      $("#cat-search", overlay).addEventListener("input", (ev) => drawPicker(ev.target.value));
      $("#cat-colors", overlay).addEventListener("click", (ev) => {
        const btn = ev.target.closest(".sw");
        if (!btn) return;
        $$(".sw", overlay).forEach((s) => s.classList.remove("is-active"));
        btn.classList.add("is-active");
        $("#cat-color", overlay).value = btn.dataset.color;
      });
      $("#cat-save", overlay).addEventListener("click", async () => {
        const payload = {
          name: $("#cat-name", overlay).value.trim(),
          icon: $("#cat-icon", overlay).value,
          color: $("#cat-color", overlay).value,
          description: $("#cat-desc", overlay).value.trim(),
          containers: [...selected],
        };
        const errBox = $("#cat-error", overlay);
        if (!payload.name) { errBox.hidden = false; errBox.textContent = "Il nome è obbligatorio."; return; }
        try {
          if (cat) await api(`/api/categories/${cat.id}`, { method: "PATCH", body: payload });
          else await api("/api/categories", { method: "POST", body: payload });
          closeModal();
          toast("ok", cat ? "Categoria aggiornata" : "Categoria creata", payload.name);
          await refresh();
        } catch (err) { errBox.hidden = false; errBox.textContent = err.message; }
      });
    },
  });
}

/* ------------------------------ modale: assegna categorie ------------------------------ */

function openAssignModal(names) {
  const targets = Array.isArray(names) ? names : [names];
  const single = targets.length === 1 ? state.containers.find((c) => c.name === targets[0]) : null;
  const preset = new Set(single ? single.categories.map((c) => c.id) : []);

  openModal({
    title: single ? `Categorie di “${esc(single.name)}”` : `Assegna categorie a ${targets.length} container`,
    body: state.categories.length ? `
      <div class="picker">${state.categories.map((cat) => `
        <label class="picker-item">
          <input type="checkbox" value="${cat.id}" ${preset.has(cat.id) ? "checked" : ""}>
          <span class="swatch" style="width:9px;height:9px;border-radius:50%;background:${esc(cat.color)}"></span>
          <span>${esc(cat.icon)} ${esc(cat.name)}</span>
          <span class="pi-sub">${cat.total} container</span>
        </label>`).join("")}</div>
      ${single ? "" : `<div class="field-hint">Le categorie spuntate verranno <b>aggiunte</b> ai container selezionati; quelle non spuntate restano invariate.</div>`}
      <p class="form-error" id="assign-error" hidden></p>`
      : `<div class="empty">${icon("tag")}<h3>Nessuna categoria</h3><div>Creane una prima di assegnarla.</div></div>`,
    footer: state.categories.length ? `
      <button class="btn btn-ghost" data-act="close-modal">Annulla</button>
      <button class="btn btn-primary" id="assign-save">${icon("check")}Applica</button>`
      : `<button class="btn btn-primary" data-act="new-category">${icon("plus")}Nuova categoria</button>`,
    onMount(overlay) {
      const saveBtn = $("#assign-save", overlay);
      if (!saveBtn) return;
      saveBtn.addEventListener("click", async () => {
        const checked = $$("input[type=checkbox]", overlay).filter((b) => b.checked).map((b) => Number(b.value));
        try {
          if (single) {
            await api("/api/categories/assign", { method: "POST", body: { container: single.name, category_ids: checked } });
          } else {
            for (const id of checked) {
              const cat = state.categories.find((c) => c.id === id);
              const merged = [...new Set([...(cat?.containers || []), ...targets])];
              await api(`/api/categories/${id}/containers`, { method: "PUT", body: { containers: merged } });
            }
          }
          closeModal();
          toast("ok", "Categorie aggiornate");
          await refresh();
        } catch (err) {
          const box = $("#assign-error", overlay);
          box.hidden = false; box.textContent = err.message;
        }
      });
    },
  });
}

/* ------------------------------ cron: costruzione e parsing ------------------------------ */

function parseCron(expr) {
  const parts = String(expr || "").trim().split(/\s+/);
  if (parts.length !== 5) return { mode: "advanced" };
  const [min, hour, dom, month, dow] = parts;
  const simple = (v) => /^\d+$/.test(v);
  const time = simple(min) && simple(hour)
    ? `${String(hour).padStart(2, "0")}:${String(min).padStart(2, "0")}` : null;
  if (!time || month !== "*") return { mode: "advanced" };
  if (dom === "*" && dow === "*") return { mode: "daily", time };
  if (dom === "*" && /^[0-6](,[0-6])*$/.test(dow)) return { mode: "weekly", time, weekdays: dow.split(",") };
  if (dow === "*" && simple(dom)) return { mode: "monthly", time, day: Number(dom) };
  return { mode: "advanced" };
}

function buildCron({ mode, time, weekdays, day, raw }) {
  if (mode === "advanced") return (raw || "").trim();
  const [hour, minute] = (time || "03:00").split(":").map((v) => Number(v));
  if (mode === "daily") return `${minute} ${hour} * * *`;
  if (mode === "weekly") return `${minute} ${hour} * * ${(weekdays?.length ? weekdays : [1]).join(",")}`;
  if (mode === "monthly") return `${minute} ${hour} ${day || 1} * *`;
  return "";
}

/* ------------------------------ modale: pianificazione ------------------------------ */

function openScheduleModal(schedule = null, prefill = null) {
  const s = schedule || {};
  const targetKind = prefill?.kind || s.target_kind || "containers";
  const targetRef = prefill?.ref ?? s.target_ref ?? "";
  const presetTargets = new Set(prefill?.targets || s.targets || []);
  const cron = parseCron(s.cron_expr || "0 3 * * *");
  const triggerKind = s.trigger_kind || "cron";
  const localIso = (() => {
    const base = s.run_at ? new Date(s.run_at) : new Date(Date.now() + 3600_000);
    const pad = (n) => String(n).padStart(2, "0");
    return `${base.getFullYear()}-${pad(base.getMonth() + 1)}-${pad(base.getDate())}T${pad(base.getHours())}:${pad(base.getMinutes())}`;
  })();

  const actions = ["start", "stop", "restart", "pause", "unpause", "up", "down"];

  openModal({
    title: schedule ? `Modifica “${esc(s.name)}”` : "Nuova pianificazione",
    subtitle: `Fuso orario: ${esc(state.meta.timezone)}`,
    wide: true,
    body: `
      <div class="row2">
        <label class="field"><span>Nome</span><input id="sc-name" value="${esc(s.name || "")}" placeholder="es. Spegni media di notte" maxlength="120"></label>
        <label class="field"><span>Azione</span>
          <select id="sc-action">${actions.map((a) =>
            `<option value="${a}" ${s.action === a ? "selected" : ""}>${ACTION_LABELS[a]}</option>`).join("")}</select>
          <span class="field-hint" id="sc-action-hint"></span></label>
      </div>

      <div class="field"><span>Destinazione</span>
        <div class="segmented" id="sc-kind">
          ${[["containers", "Container"], ["category", "Categoria"], ["stack", "Stack"], ["all", "Tutti"]]
            .map(([k, l]) => `<button type="button" data-kind="${k}" class="${targetKind === k ? "is-active" : ""}">${l}</button>`).join("")}
        </div>
      </div>
      <div id="sc-target"></div>

      <div class="field"><span>Ricorrenza</span>
        <div class="segmented" id="sc-trigger">
          ${[["cron", "A orario"], ["interval", "A intervalli"], ["once", "Una volta"]]
            .map(([k, l]) => `<button type="button" data-trigger="${k}" class="${triggerKind === k ? "is-active" : ""}">${l}</button>`).join("")}
        </div>
      </div>
      <div id="sc-when"></div>

      <div id="sc-preview" class="muted"></div>
      <p class="form-error" id="sc-error" hidden></p>`,
    footer: `${schedule ? `<button class="btn btn-danger" data-act="delete-schedule" data-id="${s.id}">${icon("trash")}Elimina</button>` : ""}
      <button class="btn btn-ghost" data-act="close-modal">Annulla</button>
      <button class="btn btn-primary" id="sc-save">${icon("check")}${schedule ? "Salva" : "Crea pianificazione"}</button>`,
    onMount(overlay) {
      const form = {
        kind: targetKind, ref: String(targetRef), targets: presetTargets,
        trigger: triggerKind,
        cronMode: cron.mode, time: cron.time || "03:00",
        weekdays: new Set(cron.weekdays || ["1"]), day: cron.day || 1,
        raw: s.cron_expr || "0 3 * * *",
        interval: s.interval_minutes || 60,
        runAt: localIso,
      };

      /* --- destinazione --- */
      const drawTarget = () => {
        const host = $("#sc-target", overlay);
        if (form.kind === "all") {
          host.innerHTML = `<div class="field-hint">L'azione verrà applicata a <b>tutti</b> i container dell'host (escluso questo pannello).</div>`;
        } else if (form.kind === "category") {
          host.innerHTML = state.categories.length
            ? `<label class="field"><span>Categoria</span><select id="sc-ref">${state.categories.map((c) =>
                `<option value="${c.id}" ${String(c.id) === form.ref ? "selected" : ""}>${esc(c.icon)} ${esc(c.name)} (${c.total})</option>`).join("")}</select></label>`
            : `<div class="form-error">Nessuna categoria disponibile: creane una prima.</div>`;
        } else if (form.kind === "stack") {
          host.innerHTML = `<label class="field"><span>Stack</span><select id="sc-ref">${state.stacks.map((st) =>
            `<option value="${esc(st.name)}" ${st.name === form.ref ? "selected" : ""}>${esc(st.name)} (${st.total})</option>`).join("")}</select></label>`;
        } else {
          host.innerHTML = `<div class="field"><span>Container</span>
            <input id="sc-search" placeholder="filtra…" autocomplete="off">
            <div class="picker" id="sc-picker"></div>
            <div class="field-hint"><span id="sc-count">${form.targets.size}</span> selezionati</div></div>`;
          const picker = $("#sc-picker", overlay);
          const draw = (query = "") => {
            const q = query.toLowerCase();
            picker.innerHTML = state.containers
              .filter((c) => !q || c.name.toLowerCase().includes(q))
              .map((c) => `<label class="picker-item">
                <input type="checkbox" value="${esc(c.name)}" ${form.targets.has(c.name) ? "checked" : ""}>
                <span class="state-dot state-${esc(c.state)}"></span><span>${esc(c.name)}</span>
                <span class="pi-sub">${esc(c.stack || "")}</span></label>`).join("")
              || `<div class="muted" style="padding:10px">nessun risultato</div>`;
          };
          draw();
          picker.addEventListener("change", (ev) => {
            if (ev.target.type !== "checkbox") return;
            ev.target.checked ? form.targets.add(ev.target.value) : form.targets.delete(ev.target.value);
            $("#sc-count", overlay).textContent = form.targets.size;
          });
          $("#sc-search", overlay).addEventListener("input", (ev) => draw(ev.target.value));
        }
        const refSel = $("#sc-ref", overlay);
        if (refSel) {
          form.ref = refSel.value;
          refSel.addEventListener("change", () => { form.ref = refSel.value; });
        }
      };

      /* --- quando --- */
      const drawWhen = () => {
        const host = $("#sc-when", overlay);
        if (form.trigger === "interval") {
          host.innerHTML = `<div class="row2">
            <label class="field"><span>Ogni</span><input id="sc-interval" type="number" min="1" max="100000" value="${form.interval}"></label>
            <label class="field"><span>Unità</span><select id="sc-unit">
              <option value="1">minuti</option><option value="60">ore</option><option value="1440">giorni</option>
            </select></label></div>`;
          const unit = form.interval % 1440 === 0 ? 1440 : form.interval % 60 === 0 ? 60 : 1;
          $("#sc-unit", overlay).value = String(unit);
          $("#sc-interval", overlay).value = form.interval / unit;
        } else if (form.trigger === "once") {
          host.innerHTML = `<label class="field"><span>Data e ora</span>
            <input id="sc-runat" type="datetime-local" value="${form.runAt}"></label>
            <div class="field-hint">Dopo l'esecuzione la pianificazione si disattiva automaticamente.</div>`;
        } else {
          host.innerHTML = `
            <div class="field"><span>Schema</span>
              <div class="segmented" id="sc-cronmode">
                ${[["daily", "Giornaliero"], ["weekly", "Settimanale"], ["monthly", "Mensile"], ["advanced", "Cron"]]
                  .map(([k, l]) => `<button type="button" data-mode="${k}" class="${form.cronMode === k ? "is-active" : ""}">${l}</button>`).join("")}
              </div></div>
            <div id="sc-cronbody"></div>`;
          const body = $("#sc-cronbody", overlay);
          const drawCron = () => {
            if (form.cronMode === "advanced") {
              body.innerHTML = `<label class="field"><span>Espressione cron</span>
                <input id="sc-raw" class="mono" value="${esc(form.raw)}" placeholder="min ora giorno mese giorno-settimana">
                <span class="field-hint">5 campi standard. Esempi: <code>0 2 * * *</code> ogni notte alle 2 · <code>*/15 * * * *</code> ogni 15 minuti</span></label>`;
              $("#sc-raw", overlay).addEventListener("input", (ev) => { form.raw = ev.target.value; preview(); });
            } else if (form.cronMode === "weekly") {
              body.innerHTML = `<div class="row2">
                  <label class="field"><span>Ora</span><input id="sc-time" type="time" value="${form.time}"></label>
                  <div class="field"><span>Giorni</span><div class="weekdays" id="sc-wd">
                    ${[1, 2, 3, 4, 5, 6, 0].map((d) => `<label class="wd"><input type="checkbox" value="${d}" ${form.weekdays.has(String(d)) ? "checked" : ""}><span>${WEEKDAY_NAMES[d]}</span></label>`).join("")}
                  </div></div></div>`;
              $("#sc-wd", overlay).addEventListener("change", (ev) => {
                ev.target.checked ? form.weekdays.add(ev.target.value) : form.weekdays.delete(ev.target.value);
                preview();
              });
              bindTime();
            } else if (form.cronMode === "monthly") {
              body.innerHTML = `<div class="row2">
                <label class="field"><span>Giorno del mese</span><input id="sc-dom" type="number" min="1" max="28" value="${form.day}"></label>
                <label class="field"><span>Ora</span><input id="sc-time" type="time" value="${form.time}"></label></div>`;
              $("#sc-dom", overlay).addEventListener("input", (ev) => { form.day = Number(ev.target.value) || 1; preview(); });
              bindTime();
            } else {
              body.innerHTML = `<label class="field" style="max-width:200px"><span>Ora</span><input id="sc-time" type="time" value="${form.time}"></label>`;
              bindTime();
            }
          };
          function bindTime() {
            const field = $("#sc-time", overlay);
            if (field) field.addEventListener("input", (ev) => { form.time = ev.target.value || "03:00"; preview(); });
          }
          $("#sc-cronmode", overlay).addEventListener("click", (ev) => {
            const btn = ev.target.closest("button[data-mode]");
            if (!btn) return;
            form.cronMode = btn.dataset.mode;
            $$("#sc-cronmode button", overlay).forEach((b) => b.classList.toggle("is-active", b === btn));
            drawCron(); preview();
          });
          drawCron();
        }
        host.addEventListener("input", preview);
        host.addEventListener("change", preview);
      };

      const collect = () => {
        const kind = form.kind;
        const payload = {
          name: $("#sc-name", overlay).value.trim() || "Pianificazione",
          enabled: schedule ? s.enabled : true,
          action: $("#sc-action", overlay).value,
          target_kind: kind,
          target_ref: kind === "category" || kind === "stack" ? String($("#sc-ref", overlay)?.value || form.ref) : "",
          targets: kind === "containers" ? [...form.targets] : [],
          trigger_kind: form.trigger,
          cron_expr: "",
          interval_minutes: 60,
          run_at: null,
          timezone_name: state.meta.timezone,
        };
        if (form.trigger === "cron") {
          payload.cron_expr = buildCron({
            mode: form.cronMode,
            time: $("#sc-time", overlay)?.value || form.time,
            weekdays: [...form.weekdays].sort(),
            day: Number($("#sc-dom", overlay)?.value || form.day),
            raw: $("#sc-raw", overlay)?.value || form.raw,
          });
        } else if (form.trigger === "interval") {
          const n = Number($("#sc-interval", overlay)?.value || 1);
          const unit = Number($("#sc-unit", overlay)?.value || 1);
          payload.interval_minutes = Math.max(1, Math.round(n * unit));
        } else {
          payload.run_at = $("#sc-runat", overlay)?.value || form.runAt;
        }
        return payload;
      };

      const preview = debounce(async () => {
        const box = $("#sc-preview", overlay);
        try {
          const res = await api("/api/schedules/preview", { method: "POST", body: collect() });
          box.innerHTML = res.valid
            ? `Prossime esecuzioni: ${res.next_runs.slice(0, 3).map((d) => `<b>${fmtDT(d, state.meta.timezone)}</b>`).join(" · ") || "nessuna"}`
            : `<span style="color:var(--err)">${esc(res.error)}</span>`;
        } catch (err) { box.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`; }
      }, 260);

      const updateHint = () => {
        const value = $("#sc-action", overlay).value;
        const hint = $("#sc-action-hint", overlay);
        if (value === "up" || value === "down") {
          hint.innerHTML = state.meta.compose_cli
            ? "Richiede destinazione <b>Stack</b>."
            : "<span style=\"color:var(--warn)\">compose up/down non abilitato (COMPOSE_CLI=false).</span>";
        } else hint.textContent = "";
      };

      $("#sc-kind", overlay).addEventListener("click", (ev) => {
        const btn = ev.target.closest("button[data-kind]");
        if (!btn) return;
        form.kind = btn.dataset.kind;
        $$("#sc-kind button", overlay).forEach((b) => b.classList.toggle("is-active", b === btn));
        drawTarget(); preview();
      });
      $("#sc-trigger", overlay).addEventListener("click", (ev) => {
        const btn = ev.target.closest("button[data-trigger]");
        if (!btn) return;
        form.trigger = btn.dataset.trigger;
        $$("#sc-trigger button", overlay).forEach((b) => b.classList.toggle("is-active", b === btn));
        drawWhen(); preview();
      });
      $("#sc-action", overlay).addEventListener("change", () => { updateHint(); preview(); });
      $("#sc-name", overlay).addEventListener("input", preview);

      $("#sc-save", overlay).addEventListener("click", async () => {
        const payload = collect();
        const errBox = $("#sc-error", overlay);
        errBox.hidden = true;
        try {
          if (schedule) await api(`/api/schedules/${s.id}`, { method: "PATCH", body: payload });
          else await api("/api/schedules", { method: "POST", body: payload });
          closeModal();
          toast("ok", schedule ? "Pianificazione aggiornata" : "Pianificazione creata", payload.name);
          state.schedules = await api("/api/schedules");
          await refresh();
        } catch (err) { errBox.hidden = false; errBox.textContent = err.message; }
      });

      drawTarget(); drawWhen(); updateHint(); preview();
    },
  });
}

/* ------------------------------ modale: hook di backup ------------------------------ */

function openHookModal(hook = null) {
  const h = hook || {};
  const form = {
    kind: h.target_kind || "containers",
    ref: String(h.target_ref ?? ""),
    targets: new Set(h.targets || []),
    mode: h.mode || "stop",
  };

  openModal({
    title: hook ? `Modifica “${esc(h.name)}”` : "Nuovo hook di backup",
    subtitle: "Due URL da incollare nel tuo strumento di backup",
    wide: true,
    body: `
      <div class="row2">
        <label class="field"><span>Nome</span>
          <input id="hk-name" value="${esc(h.name || "")}" placeholder="es. Backup notturno Vaultwarden" maxlength="120"></label>
        <label class="field"><span>Ripresa automatica</span>
          <input id="hk-resume" type="number" min="0" max="10080" value="${h.resume_after_minutes ?? 120}">
          <span class="field-hint">Minuti dopo i quali riaccendo i container se il webhook <b>post</b> non arriva (backup piantato). 0 = disattivata.</span></label>
      </div>

      <div class="field"><span>Come sospendere</span>
        <div class="segmented" id="hk-mode">
          <button type="button" data-mode="stop" class="${form.mode === "stop" ? "is-active" : ""}">Spegni (stop)</button>
          <button type="button" data-mode="pause" class="${form.mode === "pause" ? "is-active" : ""}">Metti in pausa</button>
        </div>
        <span class="field-hint" id="hk-mode-hint"></span>
      </div>

      <div class="field"><span>Container da sospendere</span>
        <div class="segmented" id="hk-kind">
          ${[["containers", "Scelti a mano"], ["category", "Categoria"], ["stack", "Stack"], ["all", "Tutti"]]
            .map(([k, l]) => `<button type="button" data-kind="${k}" class="${form.kind === k ? "is-active" : ""}">${l}</button>`).join("")}
        </div>
      </div>
      <div id="hk-target"></div>
      <div class="field-hint">Il pannello non si spegne mai da solo: senza di lui nessuno potrebbe ricevere il webhook post.</div>
      <p class="form-error" id="hk-error" hidden></p>`,
    footer: `${hook ? `<button class="btn btn-danger" data-act="delete-hook" data-id="${h.id}">${icon("trash")}Elimina</button>` : ""}
      <button class="btn btn-ghost" data-act="close-modal">Annulla</button>
      <button class="btn btn-primary" id="hk-save">${icon("check")}${hook ? "Salva" : "Crea hook"}</button>`,
    onMount(overlay) {
      const hint = $("#hk-mode-hint", overlay);
      const drawHint = () => {
        hint.innerHTML = form.mode === "stop"
          ? "Spegnimento completo: più sicuro per i database, la riaccensione richiede il tempo di avvio del servizio."
          : "Congela i processi senza chiudere il container: ripresa immediata, ma i file aperti restano tali — va bene solo se il backup tollera uno stato congelato.";
      };
      drawHint();

      const drawTarget = () => {
        const host = $("#hk-target", overlay);
        if (form.kind === "all") {
          host.innerHTML = `<div class="field-hint">Verranno sospesi <b>tutti</b> i container attivi dell'host.</div>`;
        } else if (form.kind === "category") {
          host.innerHTML = state.categories.length
            ? `<label class="field"><span>Categoria</span><select id="hk-ref">${state.categories.map((c) =>
                `<option value="${c.id}" ${String(c.id) === form.ref ? "selected" : ""}>${esc(c.icon)} ${esc(c.name)} (${c.total})</option>`).join("")}</select></label>`
            : `<div class="form-error">Nessuna categoria disponibile: creane una prima.</div>`;
        } else if (form.kind === "stack") {
          host.innerHTML = state.stacks.length
            ? `<label class="field"><span>Stack</span><select id="hk-ref">${state.stacks.map((s) =>
                `<option value="${esc(s.name)}" ${s.name === form.ref ? "selected" : ""}>${esc(s.name)} (${s.total})</option>`).join("")}</select></label>`
            : `<div class="form-error">Nessuno stack rilevato.</div>`;
        } else {
          host.innerHTML = `<div class="field">
            <input id="hk-search" placeholder="filtra per nome…" autocomplete="off">
            <div class="picker" id="hk-picker"></div>
            <div class="field-hint"><span id="hk-count">${form.targets.size}</span> selezionati</div></div>`;
          const picker = $("#hk-picker", overlay);
          const draw = (query = "") => {
            const q = query.toLowerCase();
            picker.innerHTML = state.containers
              .filter((c) => !q || c.name.toLowerCase().includes(q))
              .map((c) => `<label class="picker-item">
                <input type="checkbox" value="${esc(c.name)}" ${form.targets.has(c.name) ? "checked" : ""}>
                <span class="state-dot state-${esc(c.state)}"></span><span>${esc(c.name)}</span>
                <span class="pi-sub">${esc(c.stack || "")}</span></label>`).join("")
              || `<div class="muted" style="padding:10px">nessun risultato</div>`;
          };
          draw();
          picker.addEventListener("change", (ev) => {
            if (ev.target.type !== "checkbox") return;
            ev.target.checked ? form.targets.add(ev.target.value) : form.targets.delete(ev.target.value);
            $("#hk-count", overlay).textContent = form.targets.size;
          });
          $("#hk-search", overlay).addEventListener("input", (ev) => draw(ev.target.value));
        }
        const refSel = $("#hk-ref", overlay);
        if (refSel) {
          form.ref = refSel.value;
          refSel.addEventListener("change", () => { form.ref = refSel.value; });
        }
      };
      drawTarget();

      $("#hk-mode", overlay).addEventListener("click", (ev) => {
        const btn = ev.target.closest("button[data-mode]");
        if (!btn) return;
        form.mode = btn.dataset.mode;
        $$("#hk-mode button", overlay).forEach((b) => b.classList.toggle("is-active", b === btn));
        drawHint();
      });
      $("#hk-kind", overlay).addEventListener("click", (ev) => {
        const btn = ev.target.closest("button[data-kind]");
        if (!btn) return;
        form.kind = btn.dataset.kind;
        $$("#hk-kind button", overlay).forEach((b) => b.classList.toggle("is-active", b === btn));
        drawTarget();
      });

      $("#hk-save", overlay).addEventListener("click", async () => {
        const payload = {
          name: $("#hk-name", overlay).value.trim(),
          enabled: hook ? h.enabled : true,
          target_kind: form.kind,
          target_ref: form.kind === "category" || form.kind === "stack"
            ? String($("#hk-ref", overlay)?.value || form.ref) : "",
          targets: form.kind === "containers" ? [...form.targets] : [],
          mode: form.mode,
          resume_after_minutes: Number($("#hk-resume", overlay).value || 0),
        };
        const errBox = $("#hk-error", overlay);
        errBox.hidden = true;
        if (!payload.name) { errBox.hidden = false; errBox.textContent = "Il nome è obbligatorio."; return; }
        try {
          const res = hook
            ? await api(`/api/hooks/${h.id}`, { method: "PATCH", body: payload })
            : await api("/api/hooks", { method: "POST", body: payload });
          state.hooks = res.hooks;
          closeModal();
          toast("ok", hook ? "Hook aggiornato" : "Hook creato", payload.name);
          render();
        } catch (err) { errBox.hidden = false; errBox.textContent = err.message; }
      });
    },
  });
}

/* ------------------------------ conferme ------------------------------ */

function confirmModal({ title, text, confirmLabel = "Conferma", danger = true, onConfirm }) {
  openModal({
    title: esc(title),
    body: `<div>${text}</div>`,
    footer: `<button class="btn btn-ghost" data-act="close-modal">Annulla</button>
      <button class="btn ${danger ? "btn-danger" : "btn-primary"}" id="confirm-yes">${esc(confirmLabel)}</button>`,
    onMount(overlay) {
      $("#confirm-yes", overlay).addEventListener("click", async () => { closeModal(); await onConfirm(); });
    },
  });
}

/* ------------------------------ navigazione ------------------------------ */

function setView(view) {
  state.view = view;
  $$(".nav-item").forEach((b) => b.classList.toggle("is-active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("is-active", v.dataset.view === view));
  $("#sidebar").classList.remove("is-open");
  history.replaceState(null, "", `#${view}`);
  refresh();
}

/* ------------------------------ eventi ------------------------------ */

document.addEventListener("click", async (ev) => {
  const trigger = ev.target.closest("[data-act]");

  const navBtn = ev.target.closest(".nav-item");
  if (navBtn) { setView(navBtn.dataset.view); return; }

  if (!trigger) return;
  const act = trigger.dataset.act;
  const { name, action, kind, ref, id, value, names, phase } = trigger.dataset;

  switch (act) {
    case "close-modal":
      closeModal(); break;

    case "state-filter":
      state.stateFilter = value; renderFilters(); renderContainers(); break;

    case "show-containers":
      state.filter = { kind, ref };
      setView("containers");
      break;

    case "clear-filters":
      ev.preventDefault();
      state.filter = null; state.stateFilter = "all"; state.search = ""; $("#search").value = "";
      renderFilters(); renderContainers();
      break;

    case "hook-base":
      state.hookBase = value;
      localStorage.setItem("dm-hook-base", value);
      renderHooks();
      break;

    case "copy":
      try {
        await navigator.clipboard.writeText(value);
        toast("ok", "Copiato", value);
      } catch { toast("warn", "Copia non riuscita", "seleziona l'URL e copialo a mano"); }
      break;

    case "clear-selection":
      state.selection.clear(); renderContainers(); renderBulkbar(); break;

    case "select-group": {
      const list = (names || "").split(",").filter(Boolean);
      const allSelected = list.every((n) => state.selection.has(n));
      list.forEach((n) => allSelected ? state.selection.delete(n) : state.selection.add(n));
      renderContainers(); renderBulkbar();
      break;
    }

    case "container-action":
      await doAction({ action, targets: [name], label: `${ACTION_LABELS[action]} ${name}`, busyKey: `c:${name}` });
      break;

    case "bulk": {
      const targets = [...state.selection];
      if (!targets.length) return;
      const run = () => doAction({ action, targets, label: `${ACTION_LABELS[action]} (${targets.length})` });
      if (action === "stop" && targets.length > 3) {
        confirmModal({
          title: `Fermare ${targets.length} container?`,
          text: `Verranno fermati: <b>${targets.map(esc).join(", ")}</b>.`,
          confirmLabel: "Ferma tutti", onConfirm: run,
        });
      } else await run();
      break;
    }

    case "group-action": {
      const label = kind === "category"
        ? `${ACTION_LABELS[action]} categoria`
        : `${ACTION_LABELS[action]} stack ${ref}`;
      await doAction({ action, target_kind: kind, target_ref: ref, label });
      break;
    }

    case "forget-stack":
      confirmModal({
        title: "Dimenticare lo stack?",
        text: `“${esc(ref)}” verrà tolto dall'elenco. Non potrai più ricrearlo dal pannello,
               ma i file compose sull'host restano intatti.`,
        confirmLabel: "Dimentica",
        async onConfirm() {
          try {
            await api(`/api/stacks/${encodeURIComponent(ref)}`, { method: "DELETE" });
            if (state.filter?.kind === "stack" && state.filter.ref === ref) state.filter = null;
            toast("ok", "Stack dimenticato", ref);
            await refresh();
          } catch (err) { toast("err", "Operazione non riuscita", err.message); }
        },
      });
      break;

    case "open-container":
      openContainerModal(name); break;
    case "open-logs":
      openLogsModal(name); break;

    case "assign":
      openAssignModal(name); break;
    case "bulk-assign":
      openAssignModal([...state.selection]); break;

    case "new-category":
      openCategoryModal(); break;
    case "edit-category":
      openCategoryModal(Number(id)); break;
    case "delete-category": {
      const cat = state.categories.find((c) => c.id === Number(id));
      confirmModal({
        title: "Eliminare la categoria?",
        text: `“${esc(cat?.name || id)}” verrà rimossa. I container non vengono toccati.`,
        confirmLabel: "Elimina",
        async onConfirm() {
          try {
            await api(`/api/categories/${id}`, { method: "DELETE" });
            toast("ok", "Categoria eliminata");
            await refresh();
          } catch (err) { toast("err", "Eliminazione non riuscita", err.message); }
        },
      });
      break;
    }

    case "new-schedule":
      openScheduleModal(); break;
    case "edit-schedule":
      openScheduleModal(state.schedules.find((s) => s.id === Number(id))); break;
    case "schedule-for": {
      let prefill;
      if (kind === "selection") prefill = { kind: "containers", targets: [...state.selection] };
      else if (kind === "containers") prefill = { kind: "containers", targets: [ref] };
      else prefill = { kind, ref: String(ref) };
      closeModal();
      openScheduleModal(null, prefill);
      break;
    }
    case "delete-schedule": {
      const sched = state.schedules.find((s) => s.id === Number(id));
      confirmModal({
        title: "Eliminare la pianificazione?",
        text: `“${esc(sched?.name || id)}” non verrà più eseguita.`,
        confirmLabel: "Elimina",
        async onConfirm() {
          try {
            const res = await api(`/api/schedules/${id}`, { method: "DELETE" });
            state.schedules = res.schedules;
            toast("ok", "Pianificazione eliminata");
            render();
          } catch (err) { toast("err", "Eliminazione non riuscita", err.message); }
        },
      });
      break;
    }
    case "new-hook":
      openHookModal(); break;
    case "edit-hook":
      openHookModal(state.hooks.find((h) => h.id === Number(id))); break;

    case "hook-run": {
      const hook = state.hooks.find((h) => h.id === Number(id));
      const run = async () => {
        try {
          const res = await api(`/api/hooks/${id}/run/${phase}`, { method: "POST" });
          state.hooks = res.hooks;
          reportResult(res.result, phase === "pre" ? "Sospensione" : "Ripresa");
          await refresh();
        } catch (err) { toast("err", "Chiamata non riuscita", err.message); }
      };
      if (phase === "pre") {
        confirmModal({
          title: "Sospendere adesso i container?",
          text: `È la stessa azione che eseguirà il backup: <b>${esc(hook?.target_label || "")}</b> verrà
                 ${hook?.mode === "pause" ? "messo in pausa" : "spento"}.
                 Per riaccendere usa poi «Riprendi adesso».`,
          confirmLabel: "Sospendi", onConfirm: run,
        });
      } else await run();
      break;
    }

    case "rotate-hook":
      confirmModal({
        title: "Rigenerare il token?",
        text: "I due URL attuali smetteranno di funzionare: dovrai aggiornarli nello strumento di backup.",
        confirmLabel: "Rigenera",
        async onConfirm() {
          try {
            const res = await api(`/api/hooks/${id}/rotate`, { method: "POST" });
            state.hooks = res.hooks;
            toast("ok", "Token rigenerato", "aggiorna gli URL nel backup tool");
            render();
          } catch (err) { toast("err", "Operazione non riuscita", err.message); }
        },
      });
      break;

    case "delete-hook": {
      const hook = state.hooks.find((h) => h.id === Number(id));
      confirmModal({
        title: "Eliminare l'hook?",
        text: `“${esc(hook?.name || id)}” verrà rimosso e i suoi URL smetteranno di rispondere.`,
        confirmLabel: "Elimina",
        async onConfirm() {
          try {
            const res = await api(`/api/hooks/${id}`, { method: "DELETE" });
            state.hooks = res.hooks;
            closeModal();
            toast("ok", "Hook eliminato");
            render();
          } catch (err) { toast("err", "Eliminazione non riuscita", err.message); }
        },
      });
      break;
    }

    case "run-schedule":
      try {
        const res = await api(`/api/schedules/${id}/run`, { method: "POST" });
        state.schedules = res.schedules;
        toast("info", "Pianificazione eseguita", "controlla l'esito nella scheda");
        await refresh();
      } catch (err) { toast("err", "Esecuzione non riuscita", err.message); }
      break;

    default: break;
  }
});

document.addEventListener("change", async (ev) => {
  const target = ev.target;
  if (target.dataset?.act === "select") {
    target.checked ? state.selection.add(target.dataset.name) : state.selection.delete(target.dataset.name);
    target.closest(".crow")?.classList.toggle("is-selected", target.checked);
    renderBulkbar();
    return;
  }
  if (target.dataset?.act === "toggle-schedule") {
    try {
      const res = await api(`/api/schedules/${target.dataset.id}/toggle`, { method: "POST" });
      state.schedules = res.schedules;
      render();
    } catch (err) { toast("err", "Modifica non riuscita", err.message); }
    return;
  }
  if (target.dataset?.act === "toggle-hook") {
    try {
      const res = await api(`/api/hooks/${target.dataset.id}/toggle`, { method: "POST" });
      state.hooks = res.hooks;
      render();
    } catch (err) { toast("err", "Modifica non riuscita", err.message); }
    return;
  }

  /* filtri della pagina Container */
  if (target.id === "filter-category") {
    state.filter = target.value ? { kind: "category", ref: target.value } : null;
    renderFilters(); renderContainers();
    return;
  }
  if (target.id === "filter-stack") {
    state.filter = target.value ? { kind: "stack", ref: target.value } : null;
    renderFilters(); renderContainers();
    return;
  }
  if (target.id === "group-mode") {
    state.group = target.value;
    renderContainers();
  }
});

/* ------------------------------ login ------------------------------ */

function showLogin() {
  stopAutoRefresh();
  $("#app").hidden = true;
  $("#login-screen").hidden = false;
}

function showApp() {
  $("#login-screen").hidden = true;
  $("#app").hidden = false;
  $("#btn-logout").hidden = !state.session.auth_enabled;
}

$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const data = new FormData(ev.target);
  const errBox = $("#login-error");
  errBox.hidden = true;
  try {
    await api("/api/auth/login", {
      method: "POST",
      body: { username: data.get("username"), password: data.get("password") },
    });
    state.session.authenticated = true;
    showApp();
    await boot();
  } catch (err) { errBox.hidden = false; errBox.textContent = err.message; }
});

/* ------------------------------ avvio ------------------------------ */

async function boot() {
  state.meta = await api("/api/meta");
  await refresh({ silent: false });
  startAutoRefresh();
}

async function init() {
  applyTheme(localStorage.getItem("dm-theme") || "dark");

  $("#btn-theme").addEventListener("click", () =>
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
  $("#btn-refresh").addEventListener("click", () => refresh({ silent: false }));
  $("#btn-menu").addEventListener("click", () => $("#sidebar").classList.toggle("is-open"));
  $("#auto-refresh").addEventListener("change", (ev) => {
    state.auto = ev.target.checked;
    state.auto ? startAutoRefresh() : stopAutoRefresh();
  });
  $("#search").addEventListener("input", debounce((ev) => {
    state.search = ev.target.value;
    renderFilters(); renderContainers();
  }, 180));
  $("#btn-select-all").addEventListener("click", () => {
    const visible = visibleContainers().map((c) => c.name);
    const all = visible.every((n) => state.selection.has(n));
    visible.forEach((n) => all ? state.selection.delete(n) : state.selection.add(n));
    renderContainers(); renderBulkbar();
  });
  $("#btn-new-category").addEventListener("click", () => openCategoryModal());
  $("#btn-new-schedule").addEventListener("click", () => openScheduleModal());
  $("#btn-new-hook").addEventListener("click", () => openHookModal());
  $("#btn-clear-activity").addEventListener("click", () => confirmModal({
    title: "Svuotare lo storico?",
    text: "Tutte le voci di attività verranno cancellate.",
    confirmLabel: "Svuota",
    async onConfirm() {
      await api("/api/activity", { method: "DELETE" });
      state.activity = [];
      renderActivity();
      toast("ok", "Storico svuotato");
    },
  }));
  $("#btn-logout").addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" });
    location.reload();
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { closeModal(); $("#sidebar").classList.remove("is-open"); }
    if (ev.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
      ev.preventDefault(); $("#search").focus();
    }
    if (ev.key === "r" && (ev.metaKey || ev.ctrlKey) === false &&
        !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) && !state.modalOpen) {
      refresh({ silent: false });
    }
  });

  const hash = location.hash.replace("#", "");
  if (["containers", "categories", "stacks", "schedules", "hooks", "activity"].includes(hash)) {
    state.view = hash;
    $$(".nav-item").forEach((b) => b.classList.toggle("is-active", b.dataset.view === hash));
    $$(".view").forEach((v) => v.classList.toggle("is-active", v.dataset.view === hash));
  }

  try {
    state.session = await api("/api/session");
  } catch {
    state.session = { auth_enabled: true, authenticated: false };
  }
  if (!state.session.authenticated) { showLogin(); return; }
  showApp();
  await boot();
}

init();

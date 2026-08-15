/* BUILT DNA thermocycler console - SPA shell, landing grid and control view. */

import { createCycler, tempColor } from "/cycler.js";
import { createChart, SERIES } from "/chart.js";

const app = document.getElementById("app");
const toastEl = document.getElementById("toast");

let state = { devices: [] };
let stateLoaded = false;      // guards redirects until the first fetch lands
let profiles = [];
let current = null;          // active profile object in the control view
let view = null;             // { kind, id, teardown }

// ---------------------------------------------------------------------------
//  helpers
// ---------------------------------------------------------------------------
const fmt = v => (v === null || v === undefined) ? "--" : v.toFixed(1);
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let toastTimer = null;
function toast(msg, isError = false) {
  toastEl.textContent = msg;
  toastEl.className = "toast show" + (isError ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastEl.className = "toast"; }, 3200);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function act(id, action, extra = {}) {
  // Start the lid swinging on click. Otherwise nothing moves until the next
  // telemetry push (up to POLL_INTERVAL later), which reads as a dead button.
  if (action === "open_lid" || action === "close_lid") {
    const d = deviceById(id);
    if (d) {
      d.lid_status = "in_between";
      d.lid_moving_to = action === "open_lid" ? "open" : "closed";
      if (view && view.paint) view.paint();
    }
  }
  return api(`/api/device/${encodeURIComponent(id)}/action`, {
    method: "POST", body: JSON.stringify({ action, ...extra }),
  }).catch(e => toast(e.message, true));
}

const deviceById = id => state.devices.find(d => d.id === id);

function statusPill(d) {
  if (d.error) return `<span class="pill pill-err">Error</span>`;
  if (!d.connected) return `<span class="pill pill-off">Offline</span>`;
  if (d.running) return `<span class="pill pill-run">Running</span>`;
  return `<span class="pill pill-idle">Idle</span>`;
}

// ---------------------------------------------------------------------------
//  Landing view - all connected thermocyclers
// ---------------------------------------------------------------------------
function renderLanding() {
  app.innerHTML = `
    <div class="page view">
      <div class="page-head">
        <div>
          <h2>Instruments</h2>
          <p id="dev-count"></p>
        </div>
      </div>
      <div class="grid" id="grid"></div>
    </div>`;
  const grid = document.getElementById("grid");
  const cyclers = new Map();

  function paint() {
    const devices = state.devices;
    document.getElementById("dev-count").textContent =
      `${devices.length} thermocycler${devices.length === 1 ? "" : "s"} · ` +
      `${devices.filter(d => d.running).length} running`;

    if (!devices.length) {
      grid.innerHTML = `<div class="empty">No thermocyclers found.<br>
        Connect one over USB and press <b>Scan for devices</b>.</div>`;
      return;
    }

    for (const d of devices) {
      let card = grid.querySelector(`[data-id="${CSS.escape(d.id)}"]`);
      if (!card) {
        card = document.createElement("div");
        card.className = "card";
        card.dataset.id = d.id;
        card.innerHTML = `
          <div class="card-head">
            <div style="flex:1">
              <div class="card-name"></div>
              <div class="card-port"></div>
            </div>
            <div class="card-pill"></div>
          </div>
          <div class="card-art"></div>
          <div class="card-readouts">
            <div>
              <div class="ro-label"><i class="ro-swatch" style="background:${SERIES[0].color}"></i>Block</div>
              <div class="ro-value ro-block">--<span class="unit">°C</span></div>
              <div class="ro-target ro-block-t"></div>
            </div>
            <div>
              <div class="ro-label"><i class="ro-swatch" style="background:${SERIES[1].color}"></i>Lid</div>
              <div class="ro-value ro-lid">--<span class="unit">°C</span></div>
              <div class="ro-target ro-lid-t"></div>
            </div>
          </div>
          <div class="card-status"></div>
          <div class="progress"><i style="width:0"></i></div>`;
        card.addEventListener("click", () => { location.hash = `#/device/${d.id}`; });
        grid.appendChild(card);
        cyclers.set(d.id, createCycler(card.querySelector(".card-art"), { compact: true }));
      }
      card.querySelector(".card-name").textContent = d.name;
      card.querySelector(".card-port").textContent =
        d.simulated ? "simulated instrument" : (d.port || "—");
      card.querySelector(".card-pill").innerHTML = statusPill(d);
      card.querySelector(".ro-block").innerHTML = `${fmt(d.block_current)}<span class="unit">°C</span>`;
      card.querySelector(".ro-lid").innerHTML = `${fmt(d.lid_current)}<span class="unit">°C</span>`;
      card.querySelector(".ro-block-t").textContent =
        d.block_target !== null && d.block_target !== undefined ? `target ${fmt(d.block_target)}°` : "no target";
      card.querySelector(".ro-lid-t").textContent =
        d.lid_target !== null && d.lid_target !== undefined ? `target ${fmt(d.lid_target)}°` : "no target";
      card.querySelector(".card-status").textContent =
        d.error ? d.error : (d.running ? d.run_label : `Lid ${d.lid_status}`);
      const pct = d.step_total ? (d.step_index / d.step_total) * 100 : 0;
      card.querySelector(".progress > i").style.width = `${d.running ? pct : 0}%`;
      cyclers.get(d.id).update(d);
    }

    for (const card of [...grid.children]) {
      if (card.dataset.id && !devices.some(d => d.id === card.dataset.id)) card.remove();
    }
  }

  paint();
  return { kind: "landing", paint };
}

// ---------------------------------------------------------------------------
//  Control view
// ---------------------------------------------------------------------------
function renderControl(id, initialTab = "profile") {
  const d0 = deviceById(id);
  if (!d0) {
    // Don't bounce home just because state hasn't arrived yet - wait for it.
    if (!stateLoaded) {
      app.innerHTML = `<div class="page view"><div class="empty">Connecting…</div></div>`;
      return { kind: "pending", paint: () => route() };
    }
    location.hash = "#/";
    return null;
  }

  app.innerHTML = `
    <div class="page view">
      <div class="page-head">
        <button class="btn btn-sm" id="back">← All instruments</button>
        <div style="flex:1">
          <h2 id="dev-name" title="Click to rename" style="cursor:text"></h2>
          <p id="dev-sub"></p>
        </div>
        <div id="dev-pill"></div>
        <button class="btn btn-sm" id="conn-btn"></button>
      </div>

      <div class="control-grid">
        <div>
          <div class="panel">
            <h3>Live status</h3>
            <div id="art"></div>
            <div class="stat-row" style="margin-top:10px">
              <div class="stat">
                <div class="ro-label"><i class="ro-swatch" style="background:${SERIES[0].color}"></i>Block</div>
                <div class="ro-value" id="c-block">--<span class="unit">°C</span></div>
                <div class="ro-target" id="c-block-t"></div>
              </div>
              <div class="stat">
                <div class="ro-label"><i class="ro-swatch" style="background:${SERIES[1].color}"></i>Lid</div>
                <div class="ro-value" id="c-lid">--<span class="unit">°C</span></div>
                <div class="ro-target" id="c-lid-t"></div>
              </div>
            </div>
          </div>

          <div class="panel">
            <h3>Block</h3>
            <div class="field"><label>Target (°C)</label><input type="number" id="b-temp" value="95" min="4" max="99" step="0.5"></div>
            <div class="field"><label>Hold (s, optional)</label><input type="number" id="b-hold" placeholder="—"></div>
            <div class="field"><label>Volume (µL)</label><input type="number" id="b-vol" value="25"></div>
            <button class="btn btn-primary full" id="set-block">Set block</button>
          </div>

          <div class="panel">
            <h3>Lid</h3>
            <div class="field"><label>Target (°C)</label><input type="number" id="l-temp" value="105" min="37" max="110" step="0.5"></div>
            <button class="btn full" id="set-lid">Set lid</button>
            <h3 style="margin-top:18px">Lid movement</h3>
            <div class="btn-row">
              <button class="btn" data-act="open_lid">Open</button>
              <button class="btn" data-act="close_lid">Close</button>
              <button class="btn" data-act="plate_lift">Plate lift</button>
            </div>
            <h3 style="margin-top:18px">Deactivate</h3>
            <div class="btn-row">
              <button class="btn" data-act="deactivate_block">Block</button>
              <button class="btn" data-act="deactivate_lid">Lid</button>
              <button class="btn btn-danger" data-act="deactivate_all">All</button>
            </div>
          </div>
        </div>

        <div>
          <div class="panel">
            <div class="tabs">
              <button class="tab active" data-tab="profile">PCR Profile</button>
              <button class="tab" data-tab="graph">Graph</button>
              <button class="tab" data-tab="log">Log</button>
            </div>

            <div data-pane="profile">
              <div class="field" style="align-items:flex-end">
                <div style="flex:1">
                  <div class="ro-label" style="margin-bottom:6px">Saved profile</div>
                  <select id="prof-select"></select>
                </div>
                <button class="btn btn-sm" id="save-prof">Save as…</button>
              </div>
              <p id="prof-note" style="color:var(--ink-muted);font-size:12px;margin:2px 0 14px"></p>
              <div style="max-height:330px;overflow:auto">
                <table class="stages"><thead><tr>
                  <th>Stage / step</th><th class="num">Temp °C</th><th class="num">Time</th>
                </tr></thead><tbody id="stage-body"></tbody></table>
              </div>
              <div style="display:flex;gap:9px;align-items:center;margin-top:16px">
                <button class="btn btn-primary" id="run-btn">▶ Run profile</button>
                <button class="btn btn-danger" id="stop-btn">■ Stop</button>
                <span id="run-status" style="color:var(--ink-soft);font-size:13px"></span>
              </div>
              <div class="progress" style="margin-top:12px"><i id="run-bar" style="width:0"></i></div>
            </div>

            <div data-pane="graph" hidden>
              <div class="legend">
                ${SERIES.map(s => `<span><i style="background:${s.color}"></i>${s.label}</span>`).join("")}
              </div>
              <div id="chart"></div>
            </div>

            <div data-pane="log" hidden><div class="log" id="log"></div></div>
          </div>
        </div>
      </div>
    </div>`;

  const cycler = createCycler(document.getElementById("art"));
  const chart = createChart(document.getElementById("chart"));
  document.getElementById("back").onclick = () => { location.hash = "#/"; };

  // ---- tabs (deep-linkable: #/device/<id>/graph) -------------------------
  function showTab(name) {
    app.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
    app.querySelectorAll("[data-pane]").forEach(p => { p.hidden = p.dataset.pane !== name; });
    // replaceState, not location.hash: this must not re-run the router
    history.replaceState(null, "", `#/device/${encodeURIComponent(id)}` +
      (name === "profile" ? "" : `/${name}`));
    if (name === "graph") refreshDetail();
  }
  app.querySelectorAll(".tab").forEach(tab => {
    tab.onclick = () => showTab(tab.dataset.tab);
  });

  // ---- simple actions ---------------------------------------------------
  app.querySelectorAll("[data-act]").forEach(b => {
    b.onclick = () => act(id, b.dataset.act);
  });
  document.getElementById("set-block").onclick = () => {
    const temp = parseFloat(document.getElementById("b-temp").value);
    if (Number.isNaN(temp)) return toast("Enter a block temperature", true);
    const hold = parseFloat(document.getElementById("b-hold").value);
    const vol = parseFloat(document.getElementById("b-vol").value);
    act(id, "set_block", {
      temp, hold: Number.isNaN(hold) ? null : hold,
      volume: Number.isNaN(vol) ? null : vol,
    });
  };
  document.getElementById("set-lid").onclick = () => {
    const temp = parseFloat(document.getElementById("l-temp").value);
    if (Number.isNaN(temp)) return toast("Enter a lid temperature", true);
    act(id, "set_lid", { temp });
  };
  document.getElementById("conn-btn").onclick = () => {
    const d = deviceById(id);
    act(id, d && d.connected ? "disconnect" : "connect");
  };
  document.getElementById("run-btn").onclick = () => {
    if (!current) return toast("Choose a profile first", true);
    act(id, "run_profile", { profile: current });
  };
  document.getElementById("stop-btn").onclick = () => act(id, "stop_run");

  // ---- rename -----------------------------------------------------------
  const nameEl = document.getElementById("dev-name");
  nameEl.onclick = async () => {
    const next = prompt("Name this instrument", deviceById(id).name);
    if (next === null) return;
    try {
      await api(`/api/device/${encodeURIComponent(id)}/name`,
        { method: "POST", body: JSON.stringify({ name: next }) });
      toast("Renamed");
    } catch (e) { toast(e.message, true); }
  };

  // ---- profiles ---------------------------------------------------------
  const select = document.getElementById("prof-select");
  function fillProfiles() {
    const builtin = profiles.filter(p => p.builtin);
    const saved = profiles.filter(p => !p.builtin);
    select.innerHTML =
      `<optgroup label="Standard">${builtin.map(p =>
        `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("")}</optgroup>` +
      (saved.length
        ? `<optgroup label="Saved">${saved.map(p =>
            `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("")}</optgroup>`
        : "");
    if (current) select.value = current.id;
  }
  select.onchange = () => {
    current = profiles.find(p => p.id === select.value) || null;
    paintProfile();
  };
  document.getElementById("save-prof").onclick = async () => {
    if (!current) return;
    const name = prompt("Save profile as", `${current.name} (copy)`);
    if (!name) return;
    try {
      const saved = await api("/api/profiles", {
        method: "POST", body: JSON.stringify({ ...current, name, id: null }),
      });
      profiles = (await api("/api/profiles")).profiles;
      current = profiles.find(p => p.id === saved.id) || current;
      fillProfiles(); paintProfile();
      toast(`Saved “${name}”`);
    } catch (e) { toast(e.message, true); }
  };

  function paintProfile() {
    const body = document.getElementById("stage-body");
    document.getElementById("prof-note").textContent = current ? (current.note || "") : "";
    if (!current) { body.innerHTML = ""; return; }
    // Each stage occupies a contiguous run of flat indices:
    //   base .. base + cycles*stepsPerCycle - 1
    // and step j of any cycle sits at (i - base) % stepsPerCycle === j.
    let base = 0;
    const rows = [];
    for (const stage of current.stages) {
      const per = stage.steps.length;
      rows.push(`<tr class="stage-row"><td colspan="3">${esc(stage.name)}${
        stage.cycles > 1 ? `<span class="cycles">×${stage.cycles} cycles</span>` : ""}</td></tr>`);
      stage.steps.forEach((st, j) => {
        rows.push(`<tr class="step-row" data-base="${base}" data-per="${per}"
             data-span="${stage.cycles * per}" data-j="${j}">
          <td>Step ${j + 1}</td>
          <td class="num">${st.temp.toFixed(1)}</td>
          <td class="num">${st.seconds === null || st.seconds === undefined
            ? "hold" : st.seconds + " s"}</td></tr>`);
      });
      base += stage.cycles * per;
    }
    body.innerHTML = rows.join("");
  }

  // ---- detail polling (history + log only fetched for the open device) ---
  let detailTimer = null;
  async function refreshDetail() {
    try {
      const full = await api(`/api/device/${encodeURIComponent(id)}`);
      chart.draw(full.history);
      const logEl = document.getElementById("log");
      if (logEl) {
        const atBottom = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 40;
        logEl.innerHTML = (full.log || []).map(l => {
          const ts = new Date(l.t * 1000).toLocaleTimeString();
          const mark = { tx: "»", rx: "«", err: "!", info: "·" }[l.dir] || " ";
          return `<div><span class="ts">${ts}</span><span class="${l.dir}">${mark} ${esc(l.text)}</span></div>`;
        }).join("");
        if (atBottom) logEl.scrollTop = logEl.scrollHeight;
      }
    } catch { /* device went away; the SSE stream will route us home */ }
  }
  detailTimer = setInterval(refreshDetail, 2000);
  refreshDetail();

  function paint() {
    const d = deviceById(id);
    if (!d) { location.hash = "#/"; return; }
    nameEl.textContent = d.name;
    document.getElementById("dev-sub").textContent =
      (d.simulated ? "Simulated instrument" : d.port || "—") +
      (d.profile_name ? ` · ${d.profile_name}` : "");
    document.getElementById("dev-pill").innerHTML = statusPill(d);
    document.getElementById("conn-btn").textContent = d.connected ? "Disconnect" : "Connect";
    document.getElementById("c-block").innerHTML = `${fmt(d.block_current)}<span class="unit">°C</span>`;
    document.getElementById("c-lid").innerHTML = `${fmt(d.lid_current)}<span class="unit">°C</span>`;
    document.getElementById("c-block-t").textContent =
      d.block_target !== null && d.block_target !== undefined ? `target ${fmt(d.block_target)}°` : "no target";
    document.getElementById("c-lid-t").textContent =
      d.lid_target !== null && d.lid_target !== undefined ? `target ${fmt(d.lid_target)}°` : "no target";
    document.getElementById("run-status").textContent = d.run_label;
    document.getElementById("run-btn").disabled = !d.connected || d.running;
    document.getElementById("stop-btn").disabled = !d.running;
    document.getElementById("run-bar").style.width =
      d.running && d.step_total ? `${(d.step_index / d.step_total) * 100}%` : "0";
    cycler.update(d);

    document.querySelectorAll(".stages tr.active").forEach(r => r.classList.remove("active"));
    if (d.running && d.run_phase !== "preheat") {
      const i = d.step_index;
      for (const r of document.querySelectorAll(".stages .step-row")) {
        const base = +r.dataset.base, span = +r.dataset.span, per = +r.dataset.per;
        if (i >= base && i < base + span && (i - base) % per === +r.dataset.j) {
          r.classList.add("active");
          r.scrollIntoView({ block: "nearest" });
          break;
        }
      }
    }
  }

  fillProfiles();
  if (!current) { current = profiles[0] || null; if (current) select.value = current.id; }
  paintProfile();
  paint();
  if (initialTab !== "profile") showTab(initialTab);

  return {
    kind: "control", id, paint,
    teardown: () => clearInterval(detailTimer),
  };
}

// ---------------------------------------------------------------------------
//  Router + live stream
// ---------------------------------------------------------------------------
function route() {
  if (view && view.teardown) view.teardown();
  const hash = location.hash || "#/";
  const m = hash.match(/^#\/device\/([^/]+)(?:\/(profile|graph|log))?$/);
  view = m ? renderControl(decodeURIComponent(m[1]), m[2] || "profile") : renderLanding();
}

function onState(next) {
  state = next;
  stateLoaded = true;
  if (view && view.paint) view.paint();
}

let stream = null;
function connectStream() {
  stream = new EventSource("/api/events");
  stream.onmessage = ev => {
    try { onState(JSON.parse(ev.data)); } catch { /* ignore malformed frame */ }
  };
  stream.onerror = () => {
    stream.close();
    setTimeout(connectStream, 2000);        // server restarted: retry quietly
  };
}
// Release the stream immediately on navigate/reload. Without this each reload
// leaks a connection and the browser's per-host cap starves the JSON API.
addEventListener("pagehide", () => { if (stream) stream.close(); });

document.getElementById("scan-btn").onclick = async ev => {
  const btn = ev.currentTarget;
  btn.disabled = true; btn.textContent = "Scanning…";
  try {
    const res = await api("/api/scan", { method: "POST" });
    toast(res.found.length
      ? `Found ${res.found.length} instrument${res.found.length === 1 ? "" : "s"}`
      : "No new instruments found");
  } catch (e) { toast(e.message, true); }
  btn.disabled = false; btn.textContent = "Scan for devices";
};

(async function start() {
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      [state, profiles] = await Promise.all([
        api("/api/state"),
        api("/api/profiles").then(r => r.profiles),
      ]);
      stateLoaded = true;
      break;
    } catch (e) {
      if (attempt === 3) toast(`Cannot reach the server: ${e.message}`, true);
      else await new Promise(r => setTimeout(r, 400 * (attempt + 1)));
    }
  }
  window.addEventListener("hashchange", route);
  route();
  connectStream();
})();

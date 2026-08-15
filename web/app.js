/* BUILT DNA thermocycler console - SPA shell, landing grid, control view,
   profile editor and profile manager. */

import { createCycler } from "/cycler.js";
import { createChart, SERIES } from "/chart.js";
import { confirmDialog, promptDialog } from "/ui.js";
import { fmtTemp, unitLabel, onUnitChange } from "/units.js";
import { mountFreedomButton } from "/freedom.js";
import { createQcPane } from "/qc.js";

const BLOCK_MIN = 4, BLOCK_MAX = 99;
const LID_MIN = 37, LID_MAX = 110;

const app = document.getElementById("app");
const toastEl = document.getElementById("toast");

let state = { devices: [] };
let stateLoaded = false;
let profiles = [];
let view = null;

// Editor working copy, shared across control-view renders.
let current = null;      // deep clone of the selected profile
let sourceId = null;     // id it came from
let dirty = false;

// ---------------------------------------------------------------------------
//  helpers
// ---------------------------------------------------------------------------
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clone = o => JSON.parse(JSON.stringify(o));

let toastTimer = null;
function toast(msg, isError = false) {
  toastEl.textContent = msg;
  toastEl.className = "toast show" + (isError ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastEl.className = "toast"; }, 3200);
}

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function act(id, action, extra = {}) {
  // Start the lid swinging on click rather than on the next telemetry push.
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

/** What makes an instrument "in use" - a run, or either heater targeted. */
function busyReasons(d) {
  const out = [];
  if (d.running) out.push(`Running <b>${esc(d.profile_name || "a profile")}</b> — ${esc(d.run_label)}`);
  if (d.block_target !== null && d.block_target !== undefined)
    out.push(`Block held at <b>${d.block_target.toFixed(1)} °C</b>`);
  if (d.lid_target !== null && d.lid_target !== undefined)
    out.push(`Lid held at <b>${d.lid_target.toFixed(1)} °C</b>`);
  return out;
}

async function loadProfiles() {
  profiles = (await api("/api/profiles")).profiles;
}

function selectProfile(id) {
  const p = profiles.find(x => x.id === id);
  if (!p) return;
  current = clone(p);
  sourceId = p.id;
  dirty = false;
}

// ---------------------------------------------------------------------------
//  Landing view
// ---------------------------------------------------------------------------
function renderLanding() {
  app.innerHTML = `
    <div class="page view">
      <div class="page-head">
        <div>
          <h2>Instruments</h2>
          <p id="dev-count"></p>
        </div>
        <div style="flex:1"></div>
        <a class="btn btn-sm" href="#/profiles">Manage profiles</a>
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
              <div class="ro-value ro-block"></div>
              <div class="ro-target ro-block-t"></div>
            </div>
            <div>
              <div class="ro-label"><i class="ro-swatch" style="background:${SERIES[1].color}"></i>Lid</div>
              <div class="ro-value ro-lid"></div>
              <div class="ro-target ro-lid-t"></div>
            </div>
          </div>
          <div class="card-status"></div>
          <div class="progress"><i style="width:0"></i></div>`;
        card.addEventListener("click", () => { location.hash = `#/device/${d.id}`; });
        grid.appendChild(card);
        cyclers.set(d.id, createCycler(card.querySelector(".card-art"), { compact: true }));
      }
      const u = unitLabel();
      card.querySelector(".card-name").textContent = d.name;
      card.querySelector(".card-port").textContent =
        d.simulated ? "simulated instrument" : (d.port || "—");
      card.querySelector(".card-pill").innerHTML = statusPill(d);
      card.querySelector(".ro-block").innerHTML = `${fmtTemp(d.block_current)}<span class="unit">${u}</span>`;
      card.querySelector(".ro-lid").innerHTML = `${fmtTemp(d.lid_current)}<span class="unit">${u}</span>`;
      card.querySelector(".ro-block-t").textContent =
        d.block_target != null ? `target ${fmtTemp(d.block_target)}${u}` : "no target";
      card.querySelector(".ro-lid-t").textContent =
        d.lid_target != null ? `target ${fmtTemp(d.lid_target)}${u}` : "no target";
      card.querySelector(".card-status").textContent =
        d.error ? d.error : (d.running ? d.run_label : `Lid ${d.lid_status}`);
      const pct = d.step_total ? (d.step_index / d.step_total) * 100 : 0;
      card.querySelector(".progress > i").style.width = `${d.running ? pct : 0}%`;
      cyclers.get(d.id).update(d);
    }

    for (const card of [...grid.children]) {
      if (card.dataset.id && !state.devices.some(d => d.id === card.dataset.id)) card.remove();
    }
  }

  paint();
  return { kind: "landing", paint };
}

// ---------------------------------------------------------------------------
//  Profile manager  (#/profiles)
// ---------------------------------------------------------------------------
function renderProfiles() {
  function paint() {
    const rows = profiles.map(p => {
      const steps = p.stages.reduce((n, s) => n + s.cycles * s.steps.length, 0);
      return `
      <div class="prof-row" data-id="${esc(p.id)}">
        <div style="flex:1">
          <b>${esc(p.name)}</b>
          <span class="tag ${p.builtin ? "tag-std" : "tag-user"}">${p.builtin ? "standard" : "saved"}</span>
          <div class="meta">${p.stages.length} stages · ${steps} steps ·
            lid ${p.lid_temp ?? "—"} °C${p.preheat_lid ? " · preheat" : ""}${
              p.note ? ` · ${esc(p.note)}` : ""}</div>
        </div>
        <button class="btn btn-sm" data-open>Edit</button>
        <button class="btn btn-sm" data-dup>Duplicate</button>
        ${p.builtin ? "" : `<button class="btn btn-sm" data-ren>Rename</button>
                            <button class="btn btn-sm btn-danger" data-del>Delete</button>`}
      </div>`;
    }).join("");

    app.innerHTML = `
      <div class="page view">
        <div class="page-head">
          <button class="btn btn-sm" id="back">← All instruments</button>
          <div style="flex:1">
            <h2>Profiles</h2>
            <p>Standard presets are read-only. Duplicate one to make it editable.</p>
          </div>
        </div>
        <div class="prof-list">${rows}</div>
      </div>`;

    document.getElementById("back").onclick = () => { location.hash = "#/"; };

    app.querySelectorAll(".prof-row").forEach(row => {
      const id = row.dataset.id;
      const p = profiles.find(x => x.id === id);
      row.querySelector("[data-open]").onclick = () => {
        selectProfile(id);
        const d = state.devices[0];
        location.hash = d ? `#/device/${d.id}` : "#/";
      };
      row.querySelector("[data-dup]").onclick = async () => {
        const name = await promptDialog({
          title: "Duplicate profile", value: `${p.name} (copy)`, ok: "Duplicate",
        });
        if (!name) return;
        try {
          await api("/api/profiles", {
            method: "POST", body: JSON.stringify({ ...clone(p), name, id: null }),
          });
          await loadProfiles();
          paint();
          toast(`Created “${name}”`);
        } catch (e) { toast(e.message, true); }
      };
      const ren = row.querySelector("[data-ren]");
      if (ren) ren.onclick = async () => {
        const name = await promptDialog({ title: "Rename profile", value: p.name, ok: "Rename" });
        if (!name) return;
        try {
          await api("/api/profiles", {
            method: "POST", body: JSON.stringify({ ...clone(p), name }),
          });
          await loadProfiles();
          if (sourceId === id) selectProfile(id);
          paint();
        } catch (e) { toast(e.message, true); }
      };
      const del = row.querySelector("[data-del]");
      if (del) del.onclick = async () => {
        const ok = await confirmDialog({
          title: "Delete profile?",
          body: `“${p.name}” will be removed from ~/.builtdna/profiles. This cannot be undone.`,
          ok: "Delete", danger: true,
        });
        if (!ok) return;
        try {
          await api(`/api/profiles/${encodeURIComponent(id)}`, { method: "DELETE" });
          await loadProfiles();
          if (sourceId === id) { current = null; sourceId = null; dirty = false; }
          paint();
          toast("Deleted");
        } catch (e) { toast(e.message, true); }
      };
    });
  }

  paint();
  return { kind: "profiles", paint: () => {} };
}

// ---------------------------------------------------------------------------
//  Control view
// ---------------------------------------------------------------------------
function renderControl(id, initialTab = "profile") {
  const d0 = deviceById(id);
  if (!d0) {
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
                <div class="ro-value" id="c-block"></div>
                <div class="ro-target" id="c-block-t"></div>
              </div>
              <div class="stat">
                <div class="ro-label"><i class="ro-swatch" style="background:${SERIES[1].color}"></i>Lid</div>
                <div class="ro-value" id="c-lid"></div>
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
              <button class="tab" data-tab="qc">Thermal QC</button>
              <button class="tab" data-tab="log">Log</button>
            </div>

            <div data-pane="profile">
              <div class="field" style="align-items:flex-end">
                <div style="flex:1">
                  <div class="ro-label" style="margin-bottom:6px">Profile</div>
                  <select id="prof-select"></select>
                </div>
                <a class="btn btn-sm" href="#/profiles">Manage</a>
              </div>
              <p id="prof-note" style="color:var(--ink-muted);font-size:12px;margin:2px 0 0"></p>

              <div class="toolbar">
                <button class="btn btn-sm" id="add-stage">+ Stage</button>
                <button class="btn btn-sm" id="add-step">+ Step</button>
                <button class="btn btn-sm" id="dup-row">Duplicate</button>
                <button class="btn btn-sm" id="del-row">Delete</button>
                <button class="btn btn-sm" id="up-row">↑</button>
                <button class="btn btn-sm" id="down-row">↓</button>
                <span class="grow"></span>
                <button class="btn btn-sm" id="save-prof">Save</button>
                <button class="btn btn-sm" id="saveas-prof">Save as…</button>
              </div>

              <div style="max-height:300px;overflow:auto">
                <table class="stages"><thead><tr>
                  <th>Stage / step</th><th class="num">Temp °C</th><th class="num">Time (s)</th>
                </tr></thead><tbody id="stage-body"></tbody></table>
              </div>
              <p style="color:var(--ink-muted);font-size:11.5px;margin:10px 0 0">
                Click any value to edit. Blank or 0 seconds holds indefinitely.
                Profile temperatures are always °C.</p>

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
                <span style="color:var(--ink-muted)">· always °C</span>
              </div>
              <div id="chart"></div>
            </div>

            <div data-pane="qc" hidden><div id="qc-host"></div></div>

            <div data-pane="log" hidden><div class="log" id="log"></div></div>
          </div>
        </div>
      </div>
    </div>`;

  const cycler = createCycler(document.getElementById("art"));
  const chart = createChart(document.getElementById("chart"));
  const qcPane = createQcPane(document.getElementById("qc-host"), id, {
    api, toast, confirmDialog, getDevice: () => deviceById(id),
  });
  document.getElementById("back").onclick = () => { location.hash = "#/"; };

  // ---- tabs -------------------------------------------------------------
  function showTab(name) {
    app.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
    app.querySelectorAll("[data-pane]").forEach(p => { p.hidden = p.dataset.pane !== name; });
    history.replaceState(null, "", `#/device/${encodeURIComponent(id)}` +
      (name === "profile" ? "" : `/${name}`));
    if (name === "graph") refreshDetail();
  }
  app.querySelectorAll(".tab").forEach(t => { t.onclick = () => showTab(t.dataset.tab); });

  // ---- simple actions ---------------------------------------------------
  app.querySelectorAll("[data-act]").forEach(b => { b.onclick = () => act(id, b.dataset.act); });
  document.getElementById("set-block").onclick = () => {
    const temp = parseFloat(document.getElementById("b-temp").value);
    if (Number.isNaN(temp) || temp < BLOCK_MIN || temp > BLOCK_MAX)
      return toast(`Block target must be ${BLOCK_MIN}–${BLOCK_MAX} °C`, true);
    const hold = parseFloat(document.getElementById("b-hold").value);
    const vol = parseFloat(document.getElementById("b-vol").value);
    act(id, "set_block", {
      temp, hold: Number.isNaN(hold) ? null : hold, volume: Number.isNaN(vol) ? null : vol,
    });
  };
  document.getElementById("set-lid").onclick = () => {
    const temp = parseFloat(document.getElementById("l-temp").value);
    if (Number.isNaN(temp) || temp < LID_MIN || temp > LID_MAX)
      return toast(`Lid target must be ${LID_MIN}–${LID_MAX} °C`, true);
    act(id, "set_lid", { temp });
  };
  document.getElementById("conn-btn").onclick = () => {
    const d = deviceById(id);
    act(id, d && d.connected ? "disconnect" : "connect");
  };
  document.getElementById("stop-btn").onclick = () => act(id, "stop_run");

  // ---- run, with a busy-machine guard -----------------------------------
  document.getElementById("run-btn").onclick = async () => {
    if (!current) return toast("Choose a profile first", true);
    const d = deviceById(id);
    if (!d) return;
    const reasons = busyReasons(d);
    if (reasons.length) {
      const ok = await confirmDialog({
        title: "This instrument is in use",
        body: `${d.name} is not idle. Starting “${current.name}” will stop whatever ` +
              `it is doing now and take over the block and lid.`,
        points: reasons,
        ok: "Override and run",
        cancel: "Leave it alone",
        danger: true,
      });
      if (!ok) return;
      if (d.running) {
        await act(id, "stop_run");
        await new Promise(r => setTimeout(r, 400));   // let the abort land first
      }
    }
    if (dirty) toast("Running unsaved edits — save the profile to keep them.");
    act(id, "run_profile", { profile: current });
  };

  // ---- rename instrument -------------------------------------------------
  document.getElementById("dev-name").onclick = async () => {
    const next = await promptDialog({
      title: "Name this instrument", value: deviceById(id).name, ok: "Rename",
    });
    if (!next) return;
    try {
      await api(`/api/device/${encodeURIComponent(id)}/name`,
        { method: "POST", body: JSON.stringify({ name: next }) });
      toast("Renamed");
    } catch (e) { toast(e.message, true); }
  };

  // ---- profile select ----------------------------------------------------
  const select = document.getElementById("prof-select");
  function fillProfiles() {
    const builtin = profiles.filter(p => p.builtin);
    const saved = profiles.filter(p => !p.builtin);
    select.innerHTML =
      `<optgroup label="Standard">${builtin.map(p =>
        `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("")}</optgroup>` +
      (saved.length ? `<optgroup label="Saved">${saved.map(p =>
        `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("")}</optgroup>` : "");
    if (sourceId) select.value = sourceId;
  }
  select.onchange = async () => {
    if (dirty) {
      const ok = await confirmDialog({
        title: "Discard unsaved edits?",
        body: `“${current.name}” has unsaved changes. Switching profiles will lose them.`,
        ok: "Discard", danger: true,
      });
      if (!ok) { select.value = sourceId; return; }
    }
    selectProfile(select.value);
    sel = null;
    paintProfile();
  };

  // ---- the editor --------------------------------------------------------
  let sel = null;               // { stage, step|null }

  function markDirty() {
    dirty = true;
    paintProfile();
  }

  function editCell(td, { value, min, max, allowBlank, integer, text, commit }) {
    td.classList.add("edit");
    td.onclick = ev => {
      ev.stopPropagation();
      if (td.classList.contains("on")) return;
      td.classList.add("on");
      const input = document.createElement("input");
      input.value = value == null ? "" : value;
      td.textContent = "";
      td.appendChild(input);
      input.focus();
      input.select();
      let done = false;
      const finish = save => {
        if (done) return;
        done = true;
        td.classList.remove("on");
        if (!save) return paintProfile();
        const raw = input.value.trim();
        if (text) { commit(raw || "Stage"); return markDirty(); }
        if (raw === "" && allowBlank) { commit(null); return markDirty(); }
        let n = parseFloat(raw);
        if (Number.isNaN(n)) { toast("Enter a number", true); return paintProfile(); }
        if (integer) n = Math.round(n);
        if (n < min || n > max) {
          toast(`Enter a value between ${min} and ${max}`, true);
          return paintProfile();
        }
        commit(n);
        markDirty();
      };
      input.onblur = () => finish(true);
      input.onkeydown = e => {
        if (e.key === "Enter") { e.preventDefault(); input.blur(); }
        if (e.key === "Escape") { e.preventDefault(); finish(false); }
      };
    };
  }

  function paintProfile() {
    const body = document.getElementById("stage-body");
    const note = document.getElementById("prof-note");
    body.innerHTML = "";
    if (!current) { note.textContent = ""; return; }

    note.innerHTML = (current.note ? esc(current.note) : "") +
      (dirty ? '<span class="dirty-dot" title="unsaved changes"></span>' : "");

    let base = 0;
    current.stages.forEach((stage, i) => {
      const per = stage.steps.length;

      const tr = document.createElement("tr");
      tr.className = "stage-row" + (sel && sel.stage === i && sel.step === null ? " sel" : "");
      const nameTd = document.createElement("td");
      nameTd.className = "name-cell";
      nameTd.textContent = stage.name;
      editCell(nameTd, { value: stage.name, text: true, commit: v => { stage.name = v; } });
      const cycTd = document.createElement("td");
      cycTd.className = "num";
      cycTd.textContent = `×${stage.cycles}`;
      editCell(cycTd, {
        value: stage.cycles, min: 1, max: 999, integer: true,
        commit: v => { stage.cycles = v; },
      });
      const spacer = document.createElement("td");
      spacer.textContent = "cycles";
      spacer.style.color = "var(--ink-muted)";
      spacer.style.fontSize = "11px";
      tr.append(nameTd, cycTd, spacer);
      tr.onclick = () => { sel = { stage: i, step: null }; paintProfile(); };
      body.appendChild(tr);

      stage.steps.forEach((st, j) => {
        const row = document.createElement("tr");
        row.className = "step-row" +
          (sel && sel.stage === i && sel.step === j ? " sel" : "");
        row.dataset.base = base;
        row.dataset.per = per;
        row.dataset.span = stage.cycles * per;
        row.dataset.j = j;

        const label = document.createElement("td");
        label.textContent = `Step ${j + 1}`;
        const tempTd = document.createElement("td");
        tempTd.className = "num";
        tempTd.textContent = st.temp.toFixed(1);
        editCell(tempTd, {
          value: st.temp, min: BLOCK_MIN, max: BLOCK_MAX,
          commit: v => { st.temp = v; },
        });
        const timeTd = document.createElement("td");
        timeTd.className = "num";
        timeTd.textContent = st.seconds == null ? "hold" : st.seconds;
        editCell(timeTd, {
          value: st.seconds, min: 0, max: 86400, integer: true, allowBlank: true,
          commit: v => { st.seconds = v === 0 ? null : v; },
        });
        row.append(label, tempTd, timeTd);
        row.onclick = () => { sel = { stage: i, step: j }; paintProfile(); };
        body.appendChild(row);
      });

      base += stage.cycles * per;
    });

    const isUser = !!(sourceId && sourceId.startsWith("user:"));
    document.getElementById("save-prof").disabled = !dirty || !isUser;
    document.getElementById("save-prof").title = isUser
      ? "Overwrite this saved profile"
      : "Standard presets are read-only — use Save as…";
    paint();
  }

  // structure operations
  const stageIndex = () => (sel ? sel.stage : current ? current.stages.length - 1 : -1);

  document.getElementById("add-stage").onclick = () => {
    if (!current) return;
    const at = sel ? sel.stage + 1 : current.stages.length;
    current.stages.splice(at, 0, {
      name: "New stage", cycles: 1, steps: [{ temp: 95, seconds: 30 }],
    });
    sel = { stage: at, step: null };
    markDirty();
  };
  document.getElementById("add-step").onclick = () => {
    if (!current) return;
    const i = stageIndex();
    if (i < 0) return toast("Add a stage first", true);
    const stage = current.stages[i];
    const at = sel && sel.step !== null ? sel.step + 1 : stage.steps.length;
    stage.steps.splice(at, 0, { temp: 60, seconds: 30 });
    sel = { stage: i, step: at };
    markDirty();
  };
  document.getElementById("dup-row").onclick = () => {
    if (!current || !sel) return toast("Select a stage or step first", true);
    if (sel.step === null) {
      current.stages.splice(sel.stage + 1, 0, clone(current.stages[sel.stage]));
      sel = { stage: sel.stage + 1, step: null };
    } else {
      const steps = current.stages[sel.stage].steps;
      steps.splice(sel.step + 1, 0, clone(steps[sel.step]));
      sel = { stage: sel.stage, step: sel.step + 1 };
    }
    markDirty();
  };
  document.getElementById("del-row").onclick = () => {
    if (!current || !sel) return toast("Select a stage or step first", true);
    if (sel.step === null) {
      if (current.stages.length === 1) return toast("A profile needs at least one stage", true);
      current.stages.splice(sel.stage, 1);
      sel = null;
    } else {
      const stage = current.stages[sel.stage];
      if (stage.steps.length === 1) {
        if (current.stages.length === 1) return toast("A profile needs at least one step", true);
        current.stages.splice(sel.stage, 1);
      } else {
        stage.steps.splice(sel.step, 1);
      }
      sel = null;
    }
    markDirty();
  };
  const move = delta => () => {
    if (!current || !sel) return toast("Select a stage or step first", true);
    if (sel.step === null) {
      const j = sel.stage + delta;
      if (j < 0 || j >= current.stages.length) return;
      [current.stages[sel.stage], current.stages[j]] = [current.stages[j], current.stages[sel.stage]];
      sel = { stage: j, step: null };
    } else {
      const steps = current.stages[sel.stage].steps;
      const j = sel.step + delta;
      if (j < 0 || j >= steps.length) return;
      [steps[sel.step], steps[j]] = [steps[j], steps[sel.step]];
      sel = { stage: sel.stage, step: j };
    }
    markDirty();
  };
  document.getElementById("up-row").onclick = move(-1);
  document.getElementById("down-row").onclick = move(1);

  document.getElementById("save-prof").onclick = async () => {
    if (!current || !sourceId || !sourceId.startsWith("user:")) return;
    try {
      const saved = await api("/api/profiles", {
        method: "POST", body: JSON.stringify({ ...current, id: sourceId }),
      });
      await loadProfiles();
      sourceId = saved.id;
      dirty = false;
      fillProfiles();
      select.value = sourceId;
      paintProfile();
      toast("Saved");
    } catch (e) { toast(e.message, true); }
  };
  document.getElementById("saveas-prof").onclick = async () => {
    if (!current) return;
    const name = await promptDialog({
      title: "Save profile as",
      body: "Saved to ~/.builtdna/profiles and added to the dropdown.",
      value: sourceId && sourceId.startsWith("user:") ? current.name : `${current.name} (edited)`,
    });
    if (!name) return;
    try {
      const saved = await api("/api/profiles", {
        method: "POST", body: JSON.stringify({ ...current, name, id: null }),
      });
      await loadProfiles();
      selectProfile(saved.id);
      fillProfiles();
      select.value = sourceId;
      paintProfile();
      toast(`Saved “${name}”`);
    } catch (e) { toast(e.message, true); }
  };

  // ---- detail polling ----------------------------------------------------
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
    } catch { /* device gone; SSE will route us home */ }
  }
  const detailTimer = setInterval(refreshDetail, 2000);
  refreshDetail();

  function paint() {
    const d = deviceById(id);
    if (!d) { location.hash = "#/"; return; }
    const u = unitLabel();
    document.getElementById("dev-name").textContent = d.name;
    document.getElementById("dev-sub").textContent =
      (d.simulated ? "Simulated instrument" : d.port || "—") +
      (d.profile_name ? ` · ${d.profile_name}` : "");
    document.getElementById("dev-pill").innerHTML = statusPill(d);
    document.getElementById("conn-btn").textContent = d.connected ? "Disconnect" : "Connect";
    document.getElementById("c-block").innerHTML = `${fmtTemp(d.block_current)}<span class="unit">${u}</span>`;
    document.getElementById("c-lid").innerHTML = `${fmtTemp(d.lid_current)}<span class="unit">${u}</span>`;
    document.getElementById("c-block-t").textContent =
      d.block_target != null ? `target ${fmtTemp(d.block_target)}${u}` : "no target";
    document.getElementById("c-lid-t").textContent =
      d.lid_target != null ? `target ${fmtTemp(d.lid_target)}${u}` : "no target";
    document.getElementById("run-status").textContent = d.run_label;
    document.getElementById("run-btn").disabled = !d.connected;
    document.getElementById("stop-btn").disabled = !d.running;
    document.getElementById("run-bar").style.width =
      d.running && d.step_total ? `${(d.step_index / d.step_total) * 100}%` : "0";
    cycler.update(d);
    qcPane.onDeviceUpdate();

    document.querySelectorAll(".stages tr.active").forEach(r => r.classList.remove("active"));
    if (d.running && d.run_phase !== "preheat" && !dirty) {
      const i = d.step_index;
      for (const r of document.querySelectorAll(".stages .step-row")) {
        const base = +r.dataset.base, span = +r.dataset.span, per = +r.dataset.per;
        if (i >= base && i < base + span && (i - base) % per === +r.dataset.j) {
          r.classList.add("active");
          break;
        }
      }
    }
  }

  if (!current) selectProfile(profiles[0] && profiles[0].id);
  fillProfiles();
  paintProfile();
  if (initialTab !== "profile") showTab(initialTab);

  return { kind: "control", id, paint, teardown: () => clearInterval(detailTimer) };
}

// ---------------------------------------------------------------------------
//  Router + live stream
// ---------------------------------------------------------------------------
function route() {
  if (view && view.teardown) view.teardown();
  const hash = location.hash || "#/";
  if (/^#\/profiles\/?$/.test(hash)) { view = renderProfiles(); return; }
  const m = hash.match(/^#\/device\/([^/]+)(?:\/(profile|graph|qc|log))?$/);
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
    try { onState(JSON.parse(ev.data)); } catch { /* malformed frame */ }
  };
  stream.onerror = () => { stream.close(); setTimeout(connectStream, 2000); };
}
addEventListener("pagehide", () => { if (stream) stream.close(); });

// Repaint readouts when the display unit flips.
onUnitChange(() => { if (view && view.paint) view.paint(); });

document.getElementById("scan-btn").onclick = async ev => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  btn.textContent = "Scanning…";
  try {
    const res = await api("/api/scan", { method: "POST" });
    toast(res.found.length
      ? `Found ${res.found.length} instrument${res.found.length === 1 ? "" : "s"}`
      : "No new instruments found");
  } catch (e) { toast(e.message, true); }
  btn.disabled = false;
  btn.textContent = "Scan for devices";
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
  mountFreedomButton();
  window.addEventListener("hashchange", route);
  route();
  connectStream();
})();

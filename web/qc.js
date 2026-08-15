/* Thermal QC pane: melting-point standard, 96-well plate map, operator steps.
   Temperatures here are always Celsius. */

const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/**
 * @param host      container element
 * @param deviceId  device to run on
 * @param deps      { api, toast, confirmDialog, getDevice }
 */
export function createQcPane(host, deviceId, deps) {
  const { api, toast, confirmDialog, getDevice } = deps;
  let meta = null;      // materials + instructions from the server
  let session = null;   // live QC session snapshot
  let pending = new Set();

  host.innerHTML = `<div class="empty">Loading QC…</div>`;

  const post = (verb, body) =>
    api(`/api/device/${encodeURIComponent(deviceId)}/${verb}`,
        { method: "POST", body: JSON.stringify(body || {}) });

  // -- setup form ----------------------------------------------------------
  function renderSetup() {
    const mats = meta.materials.map(m =>
      `<option value="${esc(m.id)}" data-mp="${m.mp}">${esc(m.name)} — ${m.mp} °C</option>`).join("");
    host.innerHTML = `
      <div class="qc-intro">
        <h4>Thermal QC by melting-point standard</h4>
        <p>A slow step-and-dwell sweep across a known melting point, with the lid
           open, read visually through clear film. Gives you <b>accuracy</b> (melt
           setpoint vs nominal) and <b>uniformity</b> (spread across the plate) —
           the second of which the single block thermistor cannot show.</p>
      </div>

      <details class="qc-help" open>
        <summary>Operator instructions</summary>
        <ol>${meta.instructions.map(s => `<li>${s}</li>`).join("")}</ol>
        <div class="qc-caution">
          ${meta.cautions.map(c => `<p>⚠ ${c}</p>`).join("")}
        </div>
      </details>

      <div class="qc-form">
        <div class="field"><label>Material</label>
          <select id="qc-mat" style="width:260px">${mats}</select></div>
        <div class="field"><label>Sweep start (°C)</label>
          <input type="number" id="qc-start" step="0.1"></div>
        <div class="field"><label>Sweep end (°C)</label>
          <input type="number" id="qc-end" step="0.1"></div>
        <div class="field"><label>Step (°C)</label>
          <input type="number" id="qc-step" value="0.5" step="0.1" min="0.1"></div>
        <div class="field"><label>Dwell per step (s)</label>
          <input type="number" id="qc-dwell" value="120" min="10"></div>
        <div class="field"><label>Operator</label>
          <input type="text" id="qc-op" placeholder="initials"></div>
        <div class="field"><label>Material lot</label>
          <input type="text" id="qc-lot" placeholder="optional"></div>
        <p id="qc-plan" style="color:var(--ink-muted);font-size:12px;margin:4px 0 12px"></p>
        <button class="btn btn-primary full" id="qc-go">Start QC sweep</button>
      </div>`;

    const matEl = document.getElementById("qc-mat");
    const startEl = document.getElementById("qc-start");
    const endEl = document.getElementById("qc-end");
    const stepEl = document.getElementById("qc-step");
    const dwellEl = document.getElementById("qc-dwell");

    function applyWindow() {
      const mp = parseFloat(matEl.selectedOptions[0].dataset.mp);
      startEl.value = (mp - 4).toFixed(1);
      endEl.value = (mp + 4).toFixed(1);
      updatePlan();
    }
    function updatePlan() {
      const a = parseFloat(startEl.value), b = parseFloat(endEl.value);
      const s = parseFloat(stepEl.value), d = parseInt(dwellEl.value, 10);
      if ([a, b, s, d].some(Number.isNaN) || s <= 0 || b <= a) {
        document.getElementById("qc-plan").textContent = "";
        return;
      }
      const n = Math.floor((b - a) / s + 1e-9) + 1;
      const mins = Math.round((n * d) / 60);
      document.getElementById("qc-plan").textContent =
        `${n} steps · about ${mins} min total run time (plus ramp).`;
    }
    matEl.onchange = applyWindow;
    [startEl, endEl, stepEl, dwellEl].forEach(el => { el.oninput = updatePlan; });
    applyWindow();

    document.getElementById("qc-go").onclick = async () => {
      const d = getDevice();
      if (!d || !d.connected) return toast("Connect the instrument first", true);
      const ok = await confirmDialog({
        title: "Open the lid before starting",
        body: "This QC is read visually, so the lid must be open and the heated "
            + "lid stays off for the whole sweep. Confirm the plate is loaded, "
            + "sealed with clear film, and the lid is open.",
        points: ["Lid open", "Clear film on", "Material solidified"],
        ok: "Start sweep",
      });
      if (!ok) return;
      try {
        session = await post("qc/start", {
          material_id: matEl.value,
          start: parseFloat(startEl.value), end: parseFloat(endEl.value),
          step: parseFloat(stepEl.value), dwell: parseInt(dwellEl.value, 10),
          operator: document.getElementById("qc-op").value,
          lot: document.getElementById("qc-lot").value,
        });
        renderRun();
      } catch (e) { toast(e.message, true); }
    };
  }

  // -- live run ------------------------------------------------------------
  function currentSetpoint() {
    const d = getDevice();
    if (!session || !d) return null;
    const i = d.step_index;
    return (d.running && i >= 0 && i < session.temps.length) ? session.temps[i] : null;
  }

  function renderRun() {
    const s = session;
    host.innerHTML = `
      <div class="qc-run-head">
        <div>
          <div class="ro-label">Material</div>
          <div style="font-size:15px;font-weight:700">${esc(s.material.name)}</div>
          <div style="color:var(--ink-muted);font-size:12px">
            nominal ${s.material.mp} °C · sweep ${s.start}–${s.end} °C
            step ${s.step} °C · dwell ${s.dwell}s</div>
        </div>
        <div style="flex:1"></div>
        <div style="text-align:right">
          <div class="ro-label">Current setpoint</div>
          <div class="ro-value" id="qc-set" style="font-size:30px">—</div>
        </div>
      </div>

      <details class="qc-help">
        <summary>Operator instructions</summary>
        <ol>${meta.instructions.map(x => `<li>${x}</li>`).join("")}</ol>
        <div class="qc-caution">${meta.cautions.map(c => `<p>⚠ ${c}</p>`).join("")}</div>
      </details>

      <p class="qc-hint">Click a well the moment it turns <b>clear</b>. The current
        setpoint is stamped against it. Click again to undo a mistake.</p>

      <div class="plate" id="qc-plate"></div>

      <div class="qc-stats" id="qc-stats"></div>
      <div class="toolbar" style="margin-top:14px">
        <button class="btn btn-sm" id="qc-all">Mark all remaining</button>
        <span class="grow"></span>
        <a class="btn btn-sm" id="qc-csv"
           href="/api/device/${encodeURIComponent(deviceId)}/qc/csv">Download CSV</a>
        <button class="btn btn-sm btn-danger" id="qc-abort">Abort</button>
        <button class="btn btn-primary btn-sm" id="qc-finish">Finish &amp; save</button>
      </div>`;

    buildPlate();

    document.getElementById("qc-all").onclick = async () => {
      const t = currentSetpoint();
      if (t === null) return toast("Sweep is not on a step yet", true);
      const remaining = s.wells.filter(w => !session.melted[w]);
      if (!remaining.length) return;
      session = await post("qc/mark", { wells: remaining, temp: t });
      buildPlate(); paintStats();
    };
    document.getElementById("qc-abort").onclick = async () => {
      const ok = await confirmDialog({
        title: "Abort QC run?", body: "The sweep stops and nothing is saved.",
        ok: "Abort", danger: true,
      });
      if (!ok) return;
      await post("qc/abort", {});
      session = null;
      renderSetup();
    };
    document.getElementById("qc-finish").onclick = async () => {
      try {
        const res = await post("qc/finish", {});
        session = res;
        toast(`Saved to ${res.saved_to}`);
        renderReport();
      } catch (e) { toast(e.message, true); }
    };
    paintStats();
    onDeviceUpdate();      // fill the setpoint now, not on the next state push
  }

  function buildPlate() {
    const plate = document.getElementById("qc-plate");
    if (!plate) return;
    const s = session;
    let html = `<div class="plate-grid">`;
    html += `<div class="plate-corner"></div>`;
    for (const c of s.cols) html += `<div class="plate-head">${c}</div>`;
    for (const r of s.rows) {
      html += `<div class="plate-head">${r}</div>`;
      for (const c of s.cols) {
        const w = `${r}${c}`;
        const rec = s.melted[w];
        html += `<button class="well${rec ? " melted" : ""}" data-w="${w}"
                   title="${w}${rec ? ` — melted at ${rec.temp} °C` : ""}">
                   ${rec ? rec.temp.toFixed(1) : ""}</button>`;
      }
    }
    html += `</div>`;
    plate.innerHTML = html;

    plate.querySelectorAll(".well").forEach(btn => {
      btn.onclick = async () => {
        const w = btn.dataset.w;
        if (pending.has(w)) return;
        pending.add(w);
        try {
          if (session.melted[w]) {
            session = await post("qc/unmark", { wells: [w] });
          } else {
            const t = currentSetpoint();
            if (t === null) { toast("Sweep is not on a step yet", true); return; }
            session = await post("qc/mark", { wells: [w], temp: t });
          }
          buildPlate(); paintStats();
        } catch (e) { toast(e.message, true); }
        finally { pending.delete(w); }
      };
    });
  }

  function paintStats() {
    const el = document.getElementById("qc-stats");
    if (!el || !session) return;
    const st = session.stats;
    if (!st.n) {
      el.innerHTML = `<span style="color:var(--ink-muted)">No wells marked yet.</span>`;
      return;
    }
    const dev = st.deviation;
    const cls = Math.abs(dev) <= 1 ? "ok" : Math.abs(dev) <= 2 ? "warn" : "crit";
    el.innerHTML = `
      <div class="qc-stat"><span>Wells observed</span><b>${st.n}</b></div>
      <div class="qc-stat"><span>Mean melt</span><b>${st.mean.toFixed(2)} °C</b></div>
      <div class="qc-stat"><span>Nominal</span><b>${st.nominal} °C</b></div>
      <div class="qc-stat"><span>Accuracy (mean − nominal)</span>
        <b class="qc-${cls}">${dev > 0 ? "+" : ""}${dev.toFixed(2)} °C</b></div>
      <div class="qc-stat"><span>Uniformity spread</span><b>${st.spread.toFixed(2)} °C</b></div>`;
  }

  function renderReport() {
    const s = session, st = s.stats;
    host.innerHTML = `
      <div class="qc-intro"><h4>QC complete — ${esc(s.material.name)}</h4>
        <p>Saved to <code>${esc(s.saved_to || "~/.builtdna/qc/")}</code></p></div>
      <div class="qc-stats" id="qc-stats"></div>
      <div class="plate" id="qc-plate"></div>
      <div class="toolbar" style="margin-top:14px">
        <a class="btn btn-sm" href="/api/device/${encodeURIComponent(deviceId)}/qc/csv">Download CSV</a>
        <button class="btn btn-sm" id="qc-new">New QC run</button>
      </div>`;
    buildPlate();
    paintStats();
    document.getElementById("qc-new").onclick = () => { session = null; renderSetup(); };
  }

  // -- lifecycle -----------------------------------------------------------
  async function init() {
    try {
      meta = await api("/api/qc/materials");
      const existing = await api(`/api/device/${encodeURIComponent(deviceId)}/qc`);
      if (existing && existing.material) {
        session = existing;
        if (existing.finished_at) renderReport(); else renderRun();
      } else {
        renderSetup();
      }
    } catch (e) {
      host.innerHTML = `<div class="empty">QC unavailable: ${esc(e.message)}</div>`;
    }
  }
  init();

  /** Called on every device state push so the setpoint readout tracks the sweep. */
  function onDeviceUpdate() {
    const el = document.getElementById("qc-set");
    if (!el || !session) return;
    const t = currentSetpoint();
    const d = getDevice();
    el.textContent = t === null ? "—" : `${t.toFixed(2)} °C`;
    el.style.color = t === null ? "var(--ink-muted)" : "var(--ink)";
    if (d && session && !d.running && !session.finished_at) {
      el.textContent = "sweep ended";
    }
  }

  return { onDeviceUpdate };
}

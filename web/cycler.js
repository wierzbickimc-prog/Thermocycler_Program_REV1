/* Animated isometric thermocycler.
 *
 * The SVG is built once and then mutated, so the lid swing and the well
 * colour changes run as CSS transitions rather than re-renders.
 *
 * Lid states come straight from M119: open / closed / in_between.  When the
 * module reports in_between we animate toward the state the last open/close
 * command asked for (lid_moving_to), which the device layer records.
 */

const NS = "http://www.w3.org/2000/svg";

// Top face of the block, as a quad: left, back, right, front.
const FACE = { a: [48, 118], b: [160, 84], c: [272, 118], d: [160, 152] };
// Slightly inset quad that the wells are laid out across.
const WELLS = { a: [72, 117], b: [160, 90], c: [248, 117], d: [160, 144] };
const ROWS = 8, COLS = 12;

const el = (name, attrs = {}) => {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
};
const pts = (...p) => p.map(([x, y]) => `${x},${y}`).join(" ");

/** Bilinear interpolation across a quad. */
function onQuad(q, u, v) {
  const x = (1 - u) * (1 - v) * q.a[0] + u * (1 - v) * q.b[0]
          + u * v * q.c[0] + (1 - u) * v * q.d[0];
  const y = (1 - u) * (1 - v) * q.a[1] + u * (1 - v) * q.b[1]
          + u * v * q.c[1] + (1 - u) * v * q.d[1];
  return [x, y];
}

/** Temperature -> well colour. Cool violet through to hot brand magenta. */
export function tempColor(t) {
  if (t === null || t === undefined || Number.isNaN(t)) return "#4C3454";
  const stops = [
    [4,   [ 90,  92, 200]],   // chilled
    [25,  [104,  76, 168]],   // ambient
    [55,  [150,  62, 190]],   // mid
    [72,  [193,  55, 210]],   // extension
    [95,  [224,  52, 227]],   // denature - brand magenta
  ];
  const v = Math.max(stops[0][0], Math.min(stops[stops.length - 1][0], t));
  for (let i = 0; i < stops.length - 1; i++) {
    const [t0, c0] = stops[i], [t1, c1] = stops[i + 1];
    if (v <= t1) {
      const f = (v - t0) / (t1 - t0);
      const c = c0.map((n, j) => Math.round(n + f * (c1[j] - n)));
      return `rgb(${c[0]},${c[1]},${c[2]})`;
    }
  }
  return "#E034E3";
}

/**
 * Build a thermocycler illustration inside `host`.
 * Returns { update(state) }.
 */
export function createCycler(host, { compact = false } = {}) {
  host.innerHTML = "";
  // The viewBox extends above y=0 because the opened lid rotates up to about
  // y=-48 around the hinge; without the headroom it spilled out of its panel.
  const svg = el("svg", {
    viewBox: "0 -64 320 279",
    class: "cycler",
    role: "img",
    "aria-label": "Thermocycler",
  });
  svg.style.width = "100%";
  svg.style.display = "block";

  // ---- defs: glow + gradients -------------------------------------------
  const defs = el("defs");
  const uid = Math.random().toString(36).slice(2, 8);
  defs.innerHTML = `
    <linearGradient id="body-${uid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#3A2740"/><stop offset="1" stop-color="#1B1020"/>
    </linearGradient>
    <linearGradient id="lid-${uid}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#54395E"/><stop offset="1" stop-color="#2B1B2E"/>
    </linearGradient>
    <linearGradient id="side-${uid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2B1B2E"/><stop offset="1" stop-color="#150C18"/>
    </linearGradient>
    <filter id="glow-${uid}" x="-60%" y="-60%" width="220%" height="220%">
      <feGaussianBlur stdDeviation="7" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>`;
  svg.appendChild(defs);

  // ---- heat halo under the block ----------------------------------------
  const halo = el("ellipse", {
    cx: 160, cy: 150, rx: 118, ry: 30,
    fill: "#E034E3", opacity: 0, filter: `url(#glow-${uid})`,
  });
  halo.style.transition = "opacity .8s ease, fill .8s ease";
  svg.appendChild(halo);

  // ---- chassis -----------------------------------------------------------
  const body = el("g");
  body.appendChild(el("polygon", {                       // front face
    points: pts(FACE.a, FACE.d, [160, 196], [48, 162]),
    fill: `url(#body-${uid})`,
  }));
  body.appendChild(el("polygon", {                       // right face
    points: pts(FACE.d, FACE.c, [272, 162], [160, 196]),
    fill: `url(#side-${uid})`,
  }));
  body.appendChild(el("polygon", {                       // top deck
    points: pts(FACE.a, FACE.b, FACE.c, FACE.d),
    fill: "#241630", stroke: "rgba(255,255,255,.10)", "stroke-width": 1,
  }));
  svg.appendChild(body);

  // brand stripe across the front bezel
  svg.appendChild(el("polygon", {
    points: pts([60, 168], [160, 199], [160, 205], [60, 174]),
    fill: "#E034E3", opacity: .55,
  }));

  // ---- wells -------------------------------------------------------------
  const wellGroup = el("g");
  const wells = [];
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const [x, y] = onQuad(WELLS, (c + 0.5) / COLS, (r + 0.5) / ROWS);
      const w = el("ellipse", { cx: x.toFixed(1), cy: y.toFixed(1), rx: 4.1, ry: 2.5, fill: "#4C3454" });
      w.style.transition = "fill .9s ease";
      wells.push(w);
      wellGroup.appendChild(w);
    }
  }
  svg.appendChild(wellGroup);

  // ---- lid ---------------------------------------------------------------
  // Hinged along the back-left edge (FACE.a -> FACE.b); the whole group
  // rotates about that edge's midpoint, which reads as the lid swinging up.
  const hinge = [(FACE.a[0] + FACE.b[0]) / 2, (FACE.a[1] + FACE.b[1]) / 2];
  const lid = el("g");
  // Animate the CSS transform *property*, not the SVG transform *attribute*.
  // Transitions on the presentation attribute do not run reliably in Chrome, so
  // the lid used to snap straight to its new angle instead of swinging.
  // transform-box:view-box makes transform-origin use viewBox user units.
  lid.style.transformBox = "view-box";
  lid.style.transformOrigin = `${hinge[0]}px ${hinge[1]}px`;
  lid.style.transform = "rotate(0deg)";
  lid.style.transition = "transform 1.6s cubic-bezier(.34,.9,.3,1)";

  // The skirt is exactly as tall as the lift, so a closed lid sits flush on
  // the deck instead of appearing to hover above it.
  const LIFT = 13;
  const la = [FACE.a[0], FACE.a[1] - LIFT], lb = [FACE.b[0], FACE.b[1] - LIFT];
  const lc = [FACE.c[0], FACE.c[1] - LIFT], ld = [FACE.d[0], FACE.d[1] - LIFT];
  lid.appendChild(el("polygon", {                        // lid front skirt
    points: pts(la, ld, [ld[0], ld[1] + LIFT], [la[0], la[1] + LIFT]),
    fill: "#1F1226",
  }));
  lid.appendChild(el("polygon", {                        // lid right skirt
    points: pts(ld, lc, [lc[0], lc[1] + LIFT], [ld[0], ld[1] + LIFT]),
    fill: "#170D1C",
  }));
  // Slightly translucent, like the real heated lid: the temperature-tinted
  // wells stay legible through it when the lid is closed.
  lid.appendChild(el("polygon", {                        // lid top
    points: pts(la, lb, lc, ld),
    fill: `url(#lid-${uid})`, "fill-opacity": .87,
    stroke: "rgba(255,255,255,.16)", "stroke-width": 1,
  }));
  lid.appendChild(el("polygon", {                        // inset detail
    points: pts([la[0] + 26, la[1]], [lb[0], lb[1] + 8], [lc[0] - 26, lc[1]], [ld[0], ld[1] - 8]),
    fill: "none", stroke: "rgba(255,255,255,.09)", "stroke-width": 1,
  }));
  const lidLamp = el("circle", { cx: 160, cy: FACE.b[1] - LIFT + 15, r: 3.4, fill: "#E034E3", opacity: .25 });
  lidLamp.style.transition = "opacity .6s ease, fill .6s ease";
  lid.appendChild(lidLamp);
  svg.appendChild(lid);

  // ---- hinge posts (drawn last so they sit over the lid edge) ------------
  svg.appendChild(el("circle", { cx: FACE.a[0] + 6, cy: FACE.a[1] - 5, r: 2.6, fill: "#0F0813" }));
  svg.appendChild(el("circle", { cx: FACE.b[0] - 4, cy: FACE.b[1] - 2, r: 2.6, fill: "#0F0813" }));

  host.appendChild(svg);
  // Keep the instrument a sensible size when its panel goes full-width in the
  // single-column layout.
  host.style.maxWidth = compact ? "300px" : "340px";
  host.style.margin = "0 auto";

  // ---- updates -----------------------------------------------------------
  let lastAngle = null;
  function update(state) {
    const block = state.block_current;
    const heating = state.block_target !== null && state.block_target !== undefined;

    // wells follow the block temperature
    const color = tempColor(block);
    for (const w of wells) w.setAttribute("fill", color);

    // halo intensity tracks how hot the block actually is
    if (block === null || block === undefined || block < 30) {
      halo.setAttribute("opacity", 0);
    } else {
      halo.setAttribute("opacity", Math.min(0.42, (block - 30) / 150).toFixed(3));
      halo.setAttribute("fill", color);
    }

    // lid angle: open swings back, in_between animates toward the pending state
    let target = state.lid_status;
    if (target === "in_between") target = state.lid_moving_to || "open";
    const angle = target === "open" ? -64 : 0;
    if (angle !== lastAngle) {
      lid.style.transform = `rotate(${angle}deg)`;
      lastAngle = angle;
    }

    lidLamp.setAttribute("opacity", state.lid_target ? .95 : .25);
    lidLamp.setAttribute("fill", state.lid_target ? "#E034E3" : "#6E6076");
    svg.setAttribute("aria-label",
      `Thermocycler, lid ${state.lid_status}, block ` +
      (block === null || block === undefined ? "unknown" : `${block.toFixed(1)} degrees`));
  }

  return { update, svg };
}

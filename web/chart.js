/* Live block + lid temperature chart.
 *
 * Two categorical series on a dark plum surface. The pair #E034E3 / #00A878
 * was validated for colour-vision deficiency (deutan dE 15.8, normal dE 40.4,
 * contrast >= 3:1 against #1B1020) rather than picked by eye. Identity is never
 * carried by colour alone: both series are direct-labelled at the line end and
 * repeated in the legend.
 */

const NS = "http://www.w3.org/2000/svg";
export const SERIES = [
  { key: "block", label: "Block", color: "#E034E3" },
  { key: "lid",   label: "Lid",   color: "#00A878" },
];

const el = (n, a = {}) => {
  const node = document.createElementNS(NS, n);
  for (const [k, v] of Object.entries(a)) node.setAttribute(k, v);
  return node;
};

const PAD = { top: 14, right: 54, bottom: 26, left: 42 };

export function createChart(host) {
  host.innerHTML = "";
  const svg = el("svg", { viewBox: "0 0 720 260", role: "img" });
  svg.style.width = "100%";
  svg.style.display = "block";
  host.appendChild(svg);

  const tip = document.createElement("div");
  Object.assign(tip.style, {
    position: "absolute", pointerEvents: "none", opacity: 0,
    background: "#3A2740", border: "1px solid rgba(255,255,255,.14)",
    borderRadius: "9px", padding: "8px 11px", fontSize: "12px",
    transition: "opacity .12s", zIndex: 5, whiteSpace: "nowrap",
    boxShadow: "0 8px 24px rgba(0,0,0,.5)",
  });
  host.style.position = "relative";
  host.appendChild(tip);

  let data = [], scales = null;

  function draw(history) {
    data = (history || []).filter(d => d && d.t !== undefined);
    svg.innerHTML = "";
    const W = 720, H = 260;
    const iw = W - PAD.left - PAD.right, ih = H - PAD.top - PAD.bottom;

    if (data.length < 2) {
      const t = el("text", {
        x: W / 2, y: H / 2, "text-anchor": "middle",
        fill: "#8C7C95", "font-size": 13,
      });
      t.textContent = "Waiting for telemetry…";
      svg.appendChild(t);
      scales = null;
      return;
    }

    const t0 = data[0].t, t1 = data[data.length - 1].t;
    const span = Math.max(30, t1 - t0);
    const vals = data.flatMap(d => [d.block, d.lid]).filter(v => v !== null && v !== undefined);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi - lo < 12) { const m = (hi + lo) / 2; lo = m - 6; hi = m + 6; }
    lo = Math.floor(lo / 10) * 10 - 4;
    hi = Math.ceil(hi / 10) * 10 + 4;

    const X = t => PAD.left + ((t - t0) / span) * iw;
    const Y = v => PAD.top + ih - ((v - lo) / (hi - lo)) * ih;
    scales = { X, Y, t0, span, lo, hi, iw, ih };

    // --- recessive grid + y axis -----------------------------------------
    const step = (hi - lo) <= 40 ? 10 : 20;
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      svg.appendChild(el("line", {
        x1: PAD.left, x2: W - PAD.right, y1: Y(v), y2: Y(v),
        stroke: "rgba(255,255,255,.07)", "stroke-width": 1,
      }));
      const lbl = el("text", {
        x: PAD.left - 9, y: Y(v) + 4, "text-anchor": "end",
        fill: "#8C7C95", "font-size": 11,
      });
      lbl.textContent = v;
      svg.appendChild(lbl);
    }

    // --- x ticks in elapsed minutes --------------------------------------
    // m:ss relative to now. Rounding these to whole minutes produced repeated
    // labels ("-2m, -2m") on short windows.
    const ticks = 5;
    for (let i = 0; i <= ticks; i++) {
      const t = t0 + (span * i) / ticks;
      const ago = Math.round(t1 - t);
      const lbl = el("text", {
        x: X(t), y: H - 7, "text-anchor": "middle", fill: "#8C7C95", "font-size": 11,
      });
      lbl.textContent = i === ticks || ago <= 0
        ? "now"
        : `-${Math.floor(ago / 60)}:${String(ago % 60).padStart(2, "0")}`;
      svg.appendChild(lbl);
    }

    // --- series ----------------------------------------------------------
    for (const s of SERIES) {
      const pts = data
        .filter(d => d[s.key] !== null && d[s.key] !== undefined)
        .map(d => `${X(d.t).toFixed(1)},${Y(d[s.key]).toFixed(1)}`);
      if (pts.length < 2) continue;
      svg.appendChild(el("polyline", {
        points: pts.join(" "), fill: "none", stroke: s.color,
        "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round",
      }));
      // direct label at the line end - secondary encoding of identity
      const last = data.filter(d => d[s.key] !== null && d[s.key] !== undefined).pop();
      if (last) {
        svg.appendChild(el("circle", {
          cx: X(last.t), cy: Y(last[s.key]), r: 3.5, fill: s.color,
          stroke: "#1B1020", "stroke-width": 2,
        }));
        const lbl = el("text", {
          x: X(last.t) + 9, y: Y(last[s.key]) + 4, fill: s.color,
          "font-size": 11, "font-weight": 600,
        });
        lbl.textContent = s.label;
        svg.appendChild(lbl);
      }
    }

    svg.appendChild(el("line", {
      x1: PAD.left, x2: W - PAD.right, y1: PAD.top + ih, y2: PAD.top + ih,
      stroke: "rgba(255,255,255,.14)", "stroke-width": 1,
    }));

    cross = el("line", {
      y1: PAD.top, y2: PAD.top + ih, stroke: "rgba(255,255,255,.28)",
      "stroke-width": 1, opacity: 0,
    });
    svg.appendChild(cross);
  }

  let cross = null;

  // --- hover layer --------------------------------------------------------
  svg.addEventListener("mousemove", ev => {
    if (!scales || !data.length) return;
    const box = svg.getBoundingClientRect();
    const x = ((ev.clientX - box.left) / box.width) * 720;
    const t = scales.t0 + ((x - PAD.left) / scales.iw) * scales.span;
    let best = data[0], bd = Infinity;
    for (const d of data) {
      const dist = Math.abs(d.t - t);
      if (dist < bd) { bd = dist; best = d; }
    }
    if (cross) {
      cross.setAttribute("x1", scales.X(best.t));
      cross.setAttribute("x2", scales.X(best.t));
      cross.setAttribute("opacity", 1);
    }
    const ago = Math.max(0, data[data.length - 1].t - best.t);
    tip.innerHTML =
      `<div style="color:#8C7C95;margin-bottom:4px">${ago < 1 ? "now" : `${ago.toFixed(0)}s ago`}</div>` +
      SERIES.map(s => {
        const v = best[s.key];
        return `<div style="display:flex;gap:8px;align-items:center">
          <i style="width:9px;height:3px;border-radius:2px;background:${s.color};display:inline-block"></i>
          <span style="color:#C4B6CB">${s.label}</span>
          <b style="margin-left:auto;font-variant-numeric:tabular-nums">${
            v === null || v === undefined ? "—" : v.toFixed(1) + " °C"}</b></div>`;
      }).join("");
    tip.style.opacity = 1;
    const px = (scales.X(best.t) / 720) * box.width;
    tip.style.left = `${Math.min(box.width - 130, Math.max(0, px + 12))}px`;
    tip.style.top = `8px`;
  });
  svg.addEventListener("mouseleave", () => {
    tip.style.opacity = 0;
    if (cross) cross.setAttribute("opacity", 0);
  });

  return { draw };
}

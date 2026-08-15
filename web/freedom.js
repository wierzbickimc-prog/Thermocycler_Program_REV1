/* "Freedom Unit" — a cheeky display-unit toggle hidden bottom-right.
 *
 * Turning it on plays an eagle sweep (bottom-left -> top-right, 1.5s) followed
 * by a suited astronaut bounding right-to-left with a flag, then flips the live
 * readouts to Fahrenheit. Turning it off just reverts, no animation.
 *
 * The astronaut is a stylised suited figure — visor down, no likeness — not a
 * depiction of any specific person.
 */

import { getUnit, setUnit } from "/units.js";

const NS = "http://www.w3.org/2000/svg";
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---------------------------------------------------------------------------
//  Artwork
// ---------------------------------------------------------------------------
function stripes(id, w, h, waveAmp) {
  // 13 stripes with a canton, drawn as a waving flag via a repeating skew.
  const rows = [];
  const sh = h / 13;
  for (let i = 0; i < 13; i++) {
    rows.push(`<rect x="0" y="${(i * sh).toFixed(2)}" width="${w}" height="${sh.toFixed(2)}"
                 fill="${i % 2 === 0 ? "#B22234" : "#FFFFFF"}"/>`);
  }
  const stars = [];
  const cw = w * 0.4, ch = sh * 7;
  for (let r = 0; r < 5; r++) {
    for (let c = 0; c < 6; c++) {
      const x = (c + 0.5) * (cw / 6), y = (r + 0.5) * (ch / 5);
      stars.push(`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${(sh * 0.17).toFixed(2)}" fill="#fff"/>`);
    }
  }
  return `<g id="${id}">
    <g>${rows.join("")}</g>
    <rect x="0" y="0" width="${cw}" height="${ch}" fill="#3C3B6E"/>
    ${stars.join("")}
    <animateTransform attributeName="transform" type="skewY" values="0;${waveAmp};0;-${waveAmp};0"
                      dur="0.9s" repeatCount="indefinite"/>
  </g>`;
}

function eagleSvg() {
  // Cartoon-vector bald eagle: heavy outlines, white head with a brow, hooked
  // beak, white tail fan, talons closed around a flagpole. Drawn facing right;
  // the flight path rotates the whole group.
  const OUT = `stroke="#2B1A0C" stroke-width="6" stroke-linejoin="round" stroke-linecap="round"`;
  return `
  <g ${OUT}>
    <!-- far wing, raised back -->
    <path d="M-18,-18 C-58,-62 -104,-116 -132,-166
             C-96,-150 -54,-108 -30,-72 L-14,-44 Z" fill="#4A2E17"/>
    <!-- tail fan -->
    <path d="M-50,20 L-116,66 L-104,40 L-146,50 L-124,22
             L-166,26 L-132,2 L-64,-2 Z" fill="#F0EEE9"/>
    <!-- body -->
    <path d="M-62,6 C-64,-42 -14,-76 36,-62 C84,-48 94,4 58,36
             C22,68 -34,58 -62,6 Z" fill="#5A3A1E"/>
    <path d="M-44,22 C-8,50 42,42 68,14 C52,54 -14,64 -44,22 Z"
          fill="#40280F" stroke="none"/>
    <!-- near wing, spread forward and up, with feather steps -->
    <path d="M4,-38 C54,-92 128,-122 190,-108
             L172,-84 L146,-92 L140,-66 L114,-74 L106,-48
             L80,-56 L70,-32 L40,-38 L26,-8 Z" fill="#6B4623"/>
    <path d="M34,-56 C78,-90 130,-106 174,-100
             C138,-70 96,-44 50,-26 Z" fill="#7C5329" stroke="none"/>
    <!-- head -->
    <path d="M24,-54 C22,-92 62,-108 92,-90 C116,-76 114,-42 86,-32
             C58,-22 28,-30 24,-54 Z" fill="#FFFFFF"/>
    <!-- beak -->
    <path d="M96,-80 L142,-66 L108,-50 C98,-56 93,-70 96,-80 Z" fill="#F2B01E"/>
    <path d="M142,-66 L128,-52 L108,-50 Z" fill="#D28E12"/>
    <!-- eye and brow -->
    <circle cx="74" cy="-74" r="5.4" fill="#171717" stroke="none"/>
    <path d="M56,-90 L88,-83"/>
    <!-- talons closed round the pole -->
    <path d="M-22,36 C-14,56 -28,72 -44,66" fill="none" stroke="#F2B01E" stroke-width="11"/>
    <path d="M-2,42 C8,60 -6,78 -24,72"   fill="none" stroke="#F2B01E" stroke-width="11"/>
  </g>`;
}

function astronautSvg() {
  return `
  <g>
    <!-- flag on a pole, held out to the side -->
    <g transform="translate(34,-96)">
      <rect x="0" y="0" width="3" height="132" fill="#D8D8DC"/>
      <g transform="translate(3,2) scale(1)">${stripes("flag-a", 78, 50, 5)}</g>
    </g>
    <!-- backpack, body, limbs -->
    <rect x="-34" y="-42" width="26" height="56" rx="9" fill="#C9CBD2"/>
    <rect x="-24" y="-46" width="48" height="66" rx="19" fill="#F2F3F6"/>
    <rect x="-20" y="14" width="17" height="42" rx="8" fill="#F2F3F6"/>
    <rect x="4" y="14" width="17" height="42" rx="8" fill="#E7E8ED"/>
    <rect x="-24" y="52" width="24" height="12" rx="5" fill="#9BA0AC"/>
    <rect x="2" y="52" width="24" height="12" rx="5" fill="#9BA0AC"/>
    <rect x="-40" y="-36" width="15" height="40" rx="7" fill="#E7E8ED"/>
    <rect x="22" y="-52" width="15" height="42" rx="7" fill="#F2F3F6"
          transform="rotate(24 29 -31)"/>
    <!-- helmet: gold visor, no face -->
    <circle cx="0" cy="-62" r="30" fill="#F2F3F6"/>
    <circle cx="0" cy="-62" r="22" fill="#3A2C10"/>
    <ellipse cx="-6" cy="-70" rx="12" ry="8" fill="#E0A020" opacity=".65"/>
    <!-- shoulder flag patch -->
    <rect x="-40" y="-30" width="13" height="9" fill="#B22234"/>
    <rect x="-40" y="-30" width="6" height="5" fill="#3C3B6E"/>
  </g>`;
}

// ---------------------------------------------------------------------------
//  Animation
// ---------------------------------------------------------------------------
function stage() {
  const el = document.createElement("div");
  el.id = "freedom-stage";
  document.body.appendChild(el);
  return el;
}

const raf = () => new Promise(r => requestAnimationFrame(r));

function animate(duration, step) {
  return new Promise(resolve => {
    const t0 = performance.now();
    const tick = now => {
      const p = Math.min(1, (now - t0) / duration);
      step(p);
      if (p < 1) requestAnimationFrame(tick);
      else resolve();
    };
    requestAnimationFrame(tick);
  });
}

/** Eagle sweeps bottom-left -> top-right, filling the screen, in 1.5s. */
async function flyEagle(host) {
  const W = innerWidth, H = innerHeight;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "-260 -110 520 220");
  svg.style.cssText =
    `position:absolute;width:${Math.max(W * 0.85, 620)}px;left:0;top:0;overflow:visible;` +
    `will-change:transform;filter:drop-shadow(0 12px 34px rgba(0,0,0,.55))`;
  // The eagle grips a pole that trails down-left behind it, flag flying from
  // the far end - it climbs to the right, so that is its wake.
  svg.innerHTML = `
    <line x1="46" y1="12" x2="-330" y2="150" stroke="#2B1A0C" stroke-width="15"
          stroke-linecap="round"/>
    <line x1="46" y1="12" x2="-330" y2="150" stroke="#B47A33" stroke-width="9"
          stroke-linecap="round"/>
    <g transform="translate(-316,146) rotate(-20) translate(0,-112)">
      ${stripes("flag-e", 186, 112, 7)}
    </g>`
    + `<g id="wings">${eagleSvg()}</g>`;
  host.appendChild(svg);

  const wings = svg.querySelector("#wings");
  const startX = -W * 0.75, startY = H * 1.05;
  const endX = W * 1.05, endY = -H * 0.55;

  await animate(1500, p => {
    const e = p * p * (3 - 2 * p);                    // smoothstep
    const x = startX + (endX - startX) * e;
    const y = startY + (endY - startY) * e;
    const flap = 1 + 0.42 * Math.sin(p * Math.PI * 8);
    svg.style.transform = `translate(${x}px, ${y}px) rotate(-30deg)`;
    wings.style.transform = `scaleY(${flap.toFixed(3)})`;
    svg.style.opacity = p > 0.9 ? String((1 - p) / 0.1) : "1";
  });
  svg.remove();
}

/** Astronaut bounds right -> left in several decaying low-gravity arcs. */
async function bounceAstronaut(host) {
  const W = innerWidth, H = innerHeight;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "-70 -110 200 190");
  svg.style.cssText =
    "position:absolute;width:250px;left:0;top:0;overflow:visible;will-change:transform;" +
    "filter:drop-shadow(0 10px 26px rgba(0,0,0,.5))";
  svg.innerHTML = astronautSvg();
  host.appendChild(svg);

  const BOUNCES = 5;
  const ground = H * 0.82;
  const startX = W + 220, endX = -260;

  await animate(3200, p => {
    const x = startX + (endX - startX) * p;
    // Each bounce is a half-sine arc; peak height decays as it crosses.
    const phase = p * BOUNCES;
    const arc = Math.abs(Math.sin(phase * Math.PI));
    const decay = 1 - 0.55 * p;
    const y = ground - arc * H * 0.42 * decay;
    const tilt = Math.cos(phase * Math.PI) * 13;
    svg.style.transform = `translate(${x}px, ${y}px) rotate(${tilt.toFixed(1)}deg)`;
    svg.style.opacity = p > 0.93 ? String((1 - p) / 0.07) : "1";
  });
  svg.remove();
}

async function playSequence() {
  const host = stage();
  try {
    await flyEagle(host);
    await raf();
    await bounceAstronaut(host);
  } finally {
    host.remove();
  }
}

// ---------------------------------------------------------------------------
//  Button
// ---------------------------------------------------------------------------
export function mountFreedomButton() {
  const btn = document.createElement("button");
  btn.id = "freedom-btn";
  btn.type = "button";
  btn.textContent = "Freedom Unit";
  btn.title = "Display readouts in Fahrenheit";
  const sync = () => btn.classList.toggle("on", getUnit() === "F");
  sync();

  let busy = false;
  btn.onclick = async () => {
    if (busy) return;
    if (getUnit() === "F") { setUnit("C"); sync(); return; }   // off: just revert
    busy = true;
    btn.disabled = true;
    try {
      if (!reduced) await playSequence();
      setUnit("F");
      sync();
    } finally {
      busy = false;
      btn.disabled = false;
    }
  };

  document.body.appendChild(btn);
}

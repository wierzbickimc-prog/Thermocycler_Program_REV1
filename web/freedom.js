/* "Freedom Unit" — a cheeky display-unit toggle hidden bottom-right.
 *
 * Turning it on plays an eagle sweep (bottom-left -> top-right, 4s) followed
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
  // A side-on bald eagle built in layers so the wings can articulate without
  // squashing the head and body.  The stepped primaries keep the silhouette
  // readable during the fast, full-screen pass.
  const OUT = `stroke="#241407" stroke-width="3.2" stroke-linejoin="round" stroke-linecap="round"`;
  return `
  <defs>
    <linearGradient id="eagle-body" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#8A5A2B"/><stop offset=".55" stop-color="#5A3418"/>
      <stop offset="1" stop-color="#321B0B"/>
    </linearGradient>
    <linearGradient id="eagle-wing" x1=".15" y1="0" x2=".8" y2="1">
      <stop offset="0" stop-color="#A16B35"/><stop offset=".48" stop-color="#70431F"/>
      <stop offset="1" stop-color="#3A210E"/>
    </linearGradient>
    <linearGradient id="eagle-white" x1="0" y1="0" x2=".8" y2="1">
      <stop offset="0" stop-color="#FFFFFF"/><stop offset=".68" stop-color="#EEECE5"/>
      <stop offset="1" stop-color="#C8C5BC"/>
    </linearGradient>
  </defs>
  <g id="eagle" ${OUT}>
    <!-- far wing: long, tapered, and slightly desaturated for depth -->
    <g id="eagle-far-wing">
      <path d="M-32,-14 C-72,-58 -112,-108 -140,-164
               C-106,-151 -72,-125 -42,-91
               C-59,-127 -68,-154 -68,-178
               C-34,-148 -12,-112 2,-73
               C4,-103 10,-127 25,-146
               C36,-107 28,-68 13,-33 Z"
            fill="#4B2A13"/>
      <path d="M-31,-20 C-62,-57 -91,-96 -112,-132
               C-73,-105 -39,-68 -12,-30 Z"
            fill="#744621" stroke="none" opacity=".72"/>
    </g>

    <!-- white tail fan, with individually notched flight feathers -->
    <path d="M-58,12 L-112,57 L-105,30 L-141,48 L-126,17
             L-163,23 L-133,-4 L-82,-17 Z" fill="url(#eagle-white)"/>
    <path d="M-125,17 L-82,-8 M-106,31 L-70,0 M-140,2 L-88,-13"
          fill="none" stroke="#B7B3AA" stroke-width="2.4" opacity=".85"/>

    <!-- compact, aerodynamic body -->
    <path d="M-79,-17 C-52,-52 -5,-60 39,-43 C72,-30 84,-1 67,25
             C45,58 -12,63 -57,36 C-82,20 -92,1 -79,-17 Z"
          fill="url(#eagle-body)"/>
    <path d="M-58,24 C-21,50 31,46 63,19 C45,53 -14,65 -58,24 Z"
          fill="#2F190A" stroke="none" opacity=".65"/>
    <path d="M-50,-26 C-25,-40 3,-43 31,-36" fill="none"
          stroke="#C58A49" stroke-width="3" opacity=".45"/>

    <!-- near wing, with a clean fan of primary feathers -->
    <g id="eagle-near-wing">
      <path d="M-22,-19 C21,-70 82,-102 151,-113
               L181,-112 L160,-89 L132,-91 L142,-67 L110,-72
               L115,-47 L83,-55 L82,-30 L49,-40 L35,-8 L7,7 Z"
            fill="url(#eagle-wing)"/>
      <path d="M5,-21 C44,-63 92,-87 150,-101
               C112,-67 72,-40 30,-18 Z"
            fill="#B2783D" stroke="none" opacity=".62"/>
      <path d="M38,-55 C75,-76 111,-91 151,-101 M26,-39 C66,-57 99,-70 135,-79
               M17,-23 C54,-36 84,-47 116,-57"
            fill="none" stroke="#D5A260" stroke-width="2.2" opacity=".36"/>
    </g>

    <!-- jagged neck transition and alert, forward-looking head -->
    <g transform="translate(21,-6) scale(.82)">
    <path d="M32,-43 L24,-51 L38,-53 L31,-64 L48,-63
             C52,-89 83,-103 109,-91 C130,-81 135,-58 121,-41
             C107,-25 78,-23 57,-32 L47,-25 L43,-39 Z"
          fill="url(#eagle-white)"/>
    <path d="M106,-83 C128,-81 147,-72 161,-61 L135,-54
             C128,-43 117,-40 109,-43 C117,-51 116,-60 108,-66 Z"
          fill="#F4B52B"/>
    <path d="M161,-61 C157,-49 146,-42 133,-44 L135,-54 Z"
          fill="#D98712"/>
    <path d="M71,-78 C86,-86 103,-84 115,-77" fill="none" stroke="#5E4429" stroke-width="4"/>
    <circle cx="96" cy="-74" r="4.5" fill="#17120B" stroke="#F7F1D8" stroke-width="1.5"/>
    <circle cx="97.5" cy="-75.5" r="1.1" fill="#FFFFFF" stroke="none"/>
    </g>

    <!-- legs and hooked talons wrapped around the pole -->
    <path d="M-20,36 C-15,48 -16,57 -25,63 M4,39 C10,51 7,61 -3,67"
          fill="none" stroke="#D99516" stroke-width="8"/>
    <path d="M-25,62 C-35,70 -45,66 -45,58 M-24,62 C-24,73 -13,77 -7,67
             M-3,66 C-9,77 -20,77 -23,68 M-2,66 C3,75 14,72 15,62"
          fill="none" stroke="#F4B52B" stroke-width="5.5"/>
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

/** Eagle sweeps bottom-left -> top-right, filling the screen, in 4 seconds. */
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

  const nearWing = svg.querySelector("#eagle-near-wing");
  const farWing = svg.querySelector("#eagle-far-wing");
  const startX = -W * 0.75, startY = H * 1.05;
  const endX = W * 1.05, endY = -H * 0.55;

  await animate(4000, p => {
    const e = p * p * (3 - 2 * p);                    // smoothstep
    const x = startX + (endX - startX) * e;
    const y = startY + (endY - startY) * e;
    const beat = Math.sin(p * Math.PI * 7);
    const nearAngle = beat * 13;
    const farAngle = beat * -8;
    const bank = Math.sin(p * Math.PI) * -5;
    svg.style.transform = `translate(${x}px, ${y}px) rotate(${-27 + bank}deg)`;
    nearWing.setAttribute("transform", `rotate(${nearAngle.toFixed(2)} -12 -14)`);
    farWing.setAttribute("transform", `rotate(${farAngle.toFixed(2)} -28 -18)`);
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
  const sync = () => {
    const on = getUnit() === "F";
    btn.classList.toggle("on", on);
    btn.setAttribute("aria-pressed", String(on));
  };
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

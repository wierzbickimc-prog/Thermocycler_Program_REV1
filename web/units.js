/* Display-unit toggle.
 *
 * DELIBERATELY DISPLAY-ONLY. Everything that is stored, sent to the hardware,
 * plotted, trended or logged stays in Celsius:
 *   - profile stages/steps and their editor inputs
 *   - the Set block / Set lid inputs
 *   - the temperature chart, its axis and tooltips
 *   - the command log and the run status line
 * Only the live readouts and their target lines follow this setting, so a unit
 * switch can never change what gets commanded or saved.
 */

const KEY = "builtdna.unit";
let unit = "C";
try {
  if (localStorage.getItem(KEY) === "F") unit = "F";
} catch { /* private mode: fall back to Celsius */ }

const listeners = new Set();

export const getUnit = () => unit;
export const unitLabel = () => (unit === "F" ? "°F" : "°C");
export const onUnitChange = fn => listeners.add(fn);

export function setUnit(next) {
  unit = next === "F" ? "F" : "C";
  try { localStorage.setItem(KEY, unit); } catch { /* ignore */ }
  listeners.forEach(fn => fn(unit));
}

/** Celsius -> the current display unit. */
export const toDisplay = c => (unit === "F" ? c * 9 / 5 + 32 : c);

/** Format a Celsius value for display; returns "--" for null/undefined. */
export function fmtTemp(c, digits = 1) {
  if (c === null || c === undefined || Number.isNaN(c)) return "--";
  return toDisplay(c).toFixed(digits);
}

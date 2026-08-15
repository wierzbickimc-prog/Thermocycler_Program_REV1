#!/usr/bin/env python3
"""Thermal QC by melting-point standard.

The operator loads a known-melting-point solid into the wells, runs a slow
step-and-dwell sweep across its melting point with the lid open, and clicks each
well as it turns from opaque to clear. Two results fall out:

  accuracy    - the block setpoint at which melting is first seen, vs the
                material's nominal melting point
  uniformity  - the spread of those setpoints across the plate, which the
                single built-in block thermistor cannot show

The sweep itself is expressed as an ordinary PCR profile, so it runs through the
same tested Worker path as everything else.

All temperatures here are Celsius. The Fahrenheit toggle in the UI is display
chrome only and never reaches this module.
"""

import csv
import io
import json
import threading
import time
from pathlib import Path

from thermocycler_core import BLOCK_MIN_C, BLOCK_MAX_C

RESULTS_DIR = Path.home() / ".builtdna" / "qc"

ROWS = "ABCDEFGH"
COLS = list(range(1, 13))
WELLS = [f"{r}{c}" for r in ROWS for c in COLS]

# Melting-point standards. Gallium is deliberately absent: it attacks aluminium
# and would damage the block.
MATERIALS = [
    {"id": "lauric",   "name": "Lauric acid (C12)",   "mp": 43.8,
     "note": "Cheap soap/cosmetic supply. Reagent grade melts sharply."},
    {"id": "myristic", "name": "Myristic acid (C14)", "mp": 54.4,
     "note": "Same family as lauric; mid-range point."},
    {"id": "palmitic", "name": "Palmitic acid (C16)", "mp": 62.9,
     "note": "Useful just below the extension band."},
    {"id": "stearic",  "name": "Stearic acid (C18)",  "mp": 69.3,
     "note": "Best all-round choice: cheapest, most available, "
             "lands in the PCR annealing/extension band."},
    {"id": "vanillin", "name": "Vanillin",            "mp": 81.5,
     "note": "High-range point. Strong vanilla smell is a useful "
             "secondary confirmation."},
]
MATERIALS_BY_ID = {m["id"]: m for m in MATERIALS}

OPERATOR_STEPS = [
    "Use <b>reagent grade</b> material (&ge;98&nbsp;%). Technical grade melts over a "
    "broad range and is useless as a standard.",
    "Load <b>20–30&nbsp;µL</b> per well — a thin layer. Deep wells build a vertical "
    "gradient and read high.",
    "Load the wells you care about: at minimum all four corners, the centre, and "
    "the middle of each edge. Filling all 96 gives the best uniformity map.",
    "Let the material solidify fully, then seal with <b>optically clear</b> film so "
    "you can see down into each well.",
    "Load the plate and <b>leave the lid open</b>. Confirm the block still heats "
    "with the lid open before relying on the procedure.",
    "Start the sweep. At each step, wait for the dwell to finish, then look down "
    "each well: <b>opaque white = still solid, clear = melted</b>.",
    "Click each well on the plate map <b>as it turns clear</b>. The current "
    "setpoint is stamped against it automatically.",
    "Read on the way <b>up only</b>. These materials supercool, so the freezing "
    "point is not reproducible.",
    "When every loaded well has melted (or the sweep ends), press Finish to write "
    "the CSV and plate map.",
]

CAUTIONS = [
    "Never use gallium: it attacks aluminium and will permanently damage the block.",
    "Results read slightly <b>high</b> with the lid open — you are observing the top "
    "of the sample while the block heats it from below. Treat this as a "
    "&ldquo;within X °C of nominal&rdquo; check, not an absolute calibration.",
]


# ---------------------------------------------------------------------------
#  Sweep construction
# ---------------------------------------------------------------------------
def suggest_window(mp, span=4.0):
    """A sensible default sweep window either side of a melting point."""
    return (round(max(BLOCK_MIN_C, mp - span), 1),
            round(min(BLOCK_MAX_C, mp + span), 1))


def build_sweep(start, end, step, dwell):
    """Express the step-and-dwell sweep as profile stages.

    One stage per temperature so the runner's step index maps 1:1 onto a
    setpoint, which is what the plate map stamps against each well.
    """
    start, end, step, dwell = float(start), float(end), float(step), int(dwell)
    if step <= 0:
        raise ValueError("Step size must be greater than zero.")
    if end <= start:
        raise ValueError("End temperature must be above the start temperature.")
    if not (BLOCK_MIN_C <= start <= BLOCK_MAX_C and BLOCK_MIN_C <= end <= BLOCK_MAX_C):
        raise ValueError(f"Sweep must stay within {BLOCK_MIN_C}-{BLOCK_MAX_C} C.")
    if dwell < 10:
        raise ValueError("Dwell must be at least 10 s for the sample to equilibrate.")

    temps, t = [], start
    while t <= end + 1e-9:
        temps.append(round(t, 2))
        t += step
    if len(temps) > 200:
        raise ValueError(f"{len(temps)} steps is too many; use a coarser step "
                         f"or a narrower window.")

    stages = [{"name": f"{temp:.2f} C", "cycles": 1,
               "steps": [{"temp": temp, "seconds": dwell}]} for temp in temps]
    return temps, stages


# ---------------------------------------------------------------------------
#  Session
# ---------------------------------------------------------------------------
class QCSession:
    def __init__(self, device_id, device_name, material_id, start, end, step,
                 dwell, operator="", lot=""):
        mat = MATERIALS_BY_ID.get(material_id)
        if mat is None:
            raise ValueError(f"Unknown material: {material_id}")
        self.temps, self.stages = build_sweep(start, end, step, dwell)

        self.device_id = device_id
        self.device_name = device_name
        self.material = mat
        self.start, self.end, self.step, self.dwell = start, end, step, dwell
        self.operator, self.lot = operator, lot
        self.started_at = time.time()
        self.finished_at = None
        self.melted = {}                 # well -> {"temp": C, "t": epoch}
        self._lock = threading.Lock()

    # -- observations ------------------------------------------------------
    def mark(self, wells, temp):
        """Stamp `temp` against each newly-melted well. Already-marked wells keep
        their first observation, since melting is not reversible on the way up."""
        with self._lock:
            for w in wells:
                if w in WELLS and w not in self.melted:
                    self.melted[w] = {"temp": round(float(temp), 2), "t": time.time()}

    def unmark(self, wells):
        with self._lock:
            for w in wells:
                self.melted.pop(w, None)

    def finish(self):
        self.finished_at = time.time()
        return self.save()

    # -- results -----------------------------------------------------------
    def stats(self):
        with self._lock:
            temps = [v["temp"] for v in self.melted.values()]
        if not temps:
            return {"n": 0}
        lo, hi = min(temps), max(temps)
        mean = sum(temps) / len(temps)
        dev = round(mean - self.material["mp"], 2)
        if dev == 0:
            dev = 0.0                    # avoid reporting "-0.0"
        return {
            "n": len(temps),
            "min": round(lo, 2), "max": round(hi, 2),
            "mean": round(mean, 2),
            "spread": round(hi - lo, 2),          # uniformity
            "deviation": dev,                                    # accuracy
            "nominal": self.material["mp"],
        }

    def snapshot(self):
        return {
            "device_id": self.device_id, "device_name": self.device_name,
            "material": self.material, "operator": self.operator, "lot": self.lot,
            "start": self.start, "end": self.end, "step": self.step,
            "dwell": self.dwell, "temps": self.temps,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "melted": dict(self.melted), "stats": self.stats(),
            "wells": WELLS, "rows": list(ROWS), "cols": COLS,
        }

    def to_csv(self):
        out = io.StringIO()
        w = csv.writer(out)
        s = self.stats()
        w.writerow(["BUILT DNA thermal QC - melting point standard"])
        w.writerow(["instrument", self.device_name])
        w.writerow(["material", self.material["name"]])
        w.writerow(["nominal_mp_C", self.material["mp"]])
        w.writerow(["operator", self.operator])
        w.writerow(["lot", self.lot])
        w.writerow(["started_utc", time.strftime("%Y-%m-%d %H:%M:%S",
                                                 time.gmtime(self.started_at))])
        w.writerow(["sweep_C", f"{self.start} to {self.end} step {self.step}"])
        w.writerow(["dwell_s", self.dwell])
        w.writerow([])
        w.writerow(["wells_observed", s.get("n", 0)])
        if s.get("n"):
            w.writerow(["mean_melt_C", s["mean"]])
            w.writerow(["deviation_from_nominal_C", s["deviation"]])
            w.writerow(["uniformity_spread_C", s["spread"]])
        w.writerow([])
        w.writerow(["well", "row", "col", "melt_setpoint_C",
                    "deviation_from_nominal_C"])
        for well in WELLS:
            rec = self.melted.get(well)
            if rec is None:
                w.writerow([well, well[0], well[1:], "", ""])
            else:
                w.writerow([well, well[0], well[1:], rec["temp"],
                            round(rec["temp"] - self.material["mp"], 2)])
        return out.getvalue()

    def save(self):
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.started_at))
        base = RESULTS_DIR / f"qc-{stamp}-{self.material['id']}"
        base.with_suffix(".json").write_text(json.dumps(self.snapshot(), indent=2))
        base.with_suffix(".csv").write_text(self.to_csv())
        return str(base.with_suffix(".csv"))


# ---------------------------------------------------------------------------
#  Registry: one live session per device
# ---------------------------------------------------------------------------
_SESSIONS = {}
_REG_LOCK = threading.Lock()


def start_session(**kw):
    session = QCSession(**kw)
    with _REG_LOCK:
        _SESSIONS[session.device_id] = session
    return session


def get_session(device_id):
    with _REG_LOCK:
        return _SESSIONS.get(device_id)


def clear_session(device_id):
    with _REG_LOCK:
        _SESSIONS.pop(device_id, None)

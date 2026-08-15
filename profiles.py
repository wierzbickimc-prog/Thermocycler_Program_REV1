#!/usr/bin/env python3
"""PCR profile library: read-only built-in presets plus the user's saved profiles.

Saved profiles live as one JSON file each in ~/.builtdna/profiles/ so they can be
copied between machines or checked into a lab notebook repo.
"""

import json
import re
import unicodedata
from pathlib import Path

from thermocycler_core import BLOCK_MIN_C, BLOCK_MAX_C

USER_DIR = Path.home() / ".builtdna" / "profiles"
SETTINGS_FILE = Path.home() / ".builtdna" / "profile_settings.json"


# ---------------------------------------------------------------------------
#  Built-in presets
# ---------------------------------------------------------------------------
def _profile(pid, name, note, stages, lid_temp=105.0, volume=25.0,
             preheat=True, lid_position="closed"):
    return {"id": pid, "name": name, "note": note, "builtin": True,
            "lid_temp": lid_temp, "volume": volume, "preheat_lid": preheat,
            "lid_position": lid_position, "stages": stages}


BUILTIN = [
    _profile(
        "std-3step", "Standard 3-step (35x)",
        "Classic denature / anneal / extend for most Taq reactions.",
        [
            {"name": "Initial denaturation", "cycles": 1,
             "steps": [{"temp": 95.0, "seconds": 180}]},
            {"name": "Cycling", "cycles": 35, "steps": [
                {"temp": 95.0, "seconds": 15},
                {"temp": 58.0, "seconds": 30},
                {"temp": 72.0, "seconds": 30},
            ]},
            {"name": "Final extension", "cycles": 1,
             "steps": [{"temp": 72.0, "seconds": 300}]},
            {"name": "Hold", "cycles": 1,
             "steps": [{"temp": 4.0, "seconds": None}]},
        ]),
    _profile(
        "fast-2step", "2-step fast (40x)",
        "Combined anneal/extend at 60 C for short amplicons.",
        [
            {"name": "Initial denaturation", "cycles": 1,
             "steps": [{"temp": 98.0, "seconds": 30}]},
            {"name": "Cycling", "cycles": 40, "steps": [
                {"temp": 98.0, "seconds": 10},
                {"temp": 60.0, "seconds": 30},
            ]},
            {"name": "Final extension", "cycles": 1,
             "steps": [{"temp": 72.0, "seconds": 120}]},
            {"name": "Hold", "cycles": 1,
             "steps": [{"temp": 4.0, "seconds": None}]},
        ]),
    _profile(
        "touchdown", "Touchdown 65 -> 55",
        "Ten cycles stepping the anneal down 1 C each, then 25 at 55 C.",
        [
            {"name": "Initial denaturation", "cycles": 1,
             "steps": [{"temp": 95.0, "seconds": 180}]},
        ] + [
            {"name": f"Touchdown {t:.0f} C", "cycles": 1, "steps": [
                {"temp": 95.0, "seconds": 15},
                {"temp": float(t), "seconds": 30},
                {"temp": 72.0, "seconds": 45},
            ]} for t in range(65, 55, -1)
        ] + [
            {"name": "Cycling at 55 C", "cycles": 25, "steps": [
                {"temp": 95.0, "seconds": 15},
                {"temp": 55.0, "seconds": 30},
                {"temp": 72.0, "seconds": 45},
            ]},
            {"name": "Final extension", "cycles": 1,
             "steps": [{"temp": 72.0, "seconds": 300}]},
            {"name": "Hold", "cycles": 1,
             "steps": [{"temp": 4.0, "seconds": None}]},
        ]),
    _profile(
        "colony", "Colony PCR",
        "Long initial lysis step to crack cells before amplification.",
        [
            {"name": "Cell lysis", "cycles": 1,
             "steps": [{"temp": 95.0, "seconds": 600}]},
            {"name": "Cycling", "cycles": 30, "steps": [
                {"temp": 95.0, "seconds": 30},
                {"temp": 55.0, "seconds": 30},
                {"temp": 72.0, "seconds": 60},
            ]},
            {"name": "Final extension", "cycles": 1,
             "steps": [{"temp": 72.0, "seconds": 300}]},
            {"name": "Hold", "cycles": 1,
             "steps": [{"temp": 4.0, "seconds": None}]},
        ]),
    _profile(
        "restriction", "Restriction digest (37 C)",
        "Long isothermal hold, then heat-kill the enzyme.",
        [
            {"name": "Digest", "cycles": 1,
             "steps": [{"temp": 37.0, "seconds": 3600}]},
            {"name": "Heat inactivation", "cycles": 1,
             "steps": [{"temp": 80.0, "seconds": 1200}]},
            {"name": "Hold", "cycles": 1,
             "steps": [{"temp": 4.0, "seconds": None}]},
        ], lid_temp=45.0, preheat=False),
]

BUILTIN_BY_ID = {p["id"]: p for p in BUILTIN}


def _disabled_builtins():
    """Return the persisted set of built-in profile ids hidden from selectors."""
    try:
        with SETTINGS_FILE.open() as f:
            data = json.load(f)
        values = data.get("disabled_builtins", []) if isinstance(data, dict) else []
        return {pid for pid in values if pid in BUILTIN_BY_ID}
    except Exception:
        return set()


def _save_disabled_builtins(disabled):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump({"disabled_builtins": sorted(disabled)}, f, indent=2)
    tmp.replace(SETTINGS_FILE)


def set_builtin_active(pid, active):
    """Persist availability for a built-in profile and return its new snapshot."""
    if pid not in BUILTIN_BY_ID:
        raise ValueError("Only standard profiles can be activated or deactivated.")
    disabled = _disabled_builtins()
    if active:
        disabled.discard(pid)
    else:
        disabled.add(pid)
    _save_disabled_builtins(disabled)
    prof = dict(BUILTIN_BY_ID[pid])
    prof["active"] = pid not in disabled
    return prof


# ---------------------------------------------------------------------------
#  Validation - shared by the loader and the HTTP layer
# ---------------------------------------------------------------------------
def validate_stages(stages):
    """Return a normalised stage list, or raise ValueError explaining why not."""
    if not isinstance(stages, list) or not stages:
        raise ValueError("Profile has no stages.")
    clean = []
    for n, stage in enumerate(stages, 1):
        if not isinstance(stage, dict):
            raise ValueError(f"Stage {n} is not an object.")
        steps = stage.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError(f"Stage {n} has no steps.")
        try:
            cycles = max(1, int(float(stage.get("cycles", 1))))
        except (TypeError, ValueError):
            raise ValueError(f"Stage {n}: cycles is not a number.")
        clean_steps = []
        for m, st in enumerate(steps, 1):
            if not isinstance(st, dict) or "temp" not in st:
                raise ValueError(f"Stage {n} step {m} has no temp.")
            try:
                temp = float(st["temp"])
            except (TypeError, ValueError):
                raise ValueError(f"Stage {n} step {m}: temp is not numeric.")
            if not (BLOCK_MIN_C <= temp <= BLOCK_MAX_C):
                raise ValueError(f"Stage {n} step {m}: {temp} C is outside "
                                 f"{BLOCK_MIN_C}-{BLOCK_MAX_C} C.")
            secs = st.get("seconds")
            if secs in (None, "", 0, "0"):
                secs = None
            else:
                try:
                    secs = int(float(secs))
                except (TypeError, ValueError):
                    raise ValueError(f"Stage {n} step {m}: seconds is not numeric.")
            clean_steps.append({"temp": temp, "seconds": secs})
        clean.append({"name": str(stage.get("name") or f"Stage {n}"),
                      "cycles": cycles, "steps": clean_steps})
    return clean


def validate_profile(data):
    """Normalise a whole profile document (stages + run options)."""
    if not isinstance(data, dict):
        raise ValueError("Profile must be a JSON object.")

    def _num(key, default, lo, hi):
        val = data.get(key, default)
        if val is None:
            return None
        try:
            val = float(val)
        except (TypeError, ValueError):
            raise ValueError(f"'{key}' is not numeric.")
        if not (lo <= val <= hi):
            raise ValueError(f"'{key}' must be between {lo} and {hi}.")
        return val

    lid_position = str(data.get("lid_position") or "closed").lower()
    if lid_position not in ("open", "closed"):
        raise ValueError("'lid_position' must be 'open' or 'closed'.")

    lid_temp = _num("lid_temp", 105.0, 37.0, 110.0)
    preheat_lid = bool(data.get("preheat_lid", True))
    # An open heated lid provides no benefit and creates an exposed hot surface.
    if lid_position == "open":
        lid_temp = None
        preheat_lid = False

    return {
        "name": str(data.get("name") or "Untitled profile"),
        "note": str(data.get("note") or ""),
        "lid_position": lid_position,
        "lid_temp": lid_temp,
        "volume": _num("volume", 25.0, 0.0, 100.0),
        "preheat_lid": preheat_lid,
        "stages": validate_stages(data.get("stages")),
    }


# ---------------------------------------------------------------------------
#  User profile storage
# ---------------------------------------------------------------------------
def _slug(name):
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "profile"


def _unique_path(slug):
    USER_DIR.mkdir(parents=True, exist_ok=True)
    path = USER_DIR / f"{slug}.json"
    n = 2
    while path.exists():
        path = USER_DIR / f"{slug}-{n}.json"
        n += 1
    return path


def list_profiles():
    """All profiles: built-ins first, then saved ones sorted by name."""
    disabled = _disabled_builtins()
    out = []
    for original in BUILTIN:
        prof = dict(original)
        prof["active"] = prof["id"] not in disabled
        out.append(prof)
    saved = []
    if USER_DIR.is_dir():
        for path in sorted(USER_DIR.glob("*.json")):
            try:
                with path.open() as f:
                    data = json.load(f)
                prof = validate_profile(data)
            except Exception:
                continue                      # skip unreadable files silently
            prof.update(id=f"user:{path.stem}", builtin=False, active=True)
            saved.append(prof)
    saved.sort(key=lambda p: p["name"].lower())
    return out + saved


def get_profile(pid):
    if pid in BUILTIN_BY_ID:
        prof = dict(BUILTIN_BY_ID[pid])
        prof["active"] = pid not in _disabled_builtins()
        return prof
    if pid.startswith("user:"):
        path = USER_DIR / f"{pid[5:]}.json"
        if path.is_file():
            with path.open() as f:
                prof = validate_profile(json.load(f))
            prof.update(id=pid, builtin=False, active=True)
            return prof
    raise KeyError(f"No such profile: {pid}")


def save_profile(data):
    """Save (or overwrite, when 'id' names an existing user profile) a profile."""
    prof = validate_profile(data)
    pid = data.get("id") or ""
    if pid.startswith("user:"):
        path = USER_DIR / f"{pid[5:]}.json"
        USER_DIR.mkdir(parents=True, exist_ok=True)
    else:
        path = _unique_path(_slug(prof["name"]))
    with path.open("w") as f:
        json.dump(prof, f, indent=2)
    prof.update(id=f"user:{path.stem}", builtin=False, active=True)
    return prof


def delete_profile(pid):
    if not pid.startswith("user:"):
        raise ValueError("Built-in profiles cannot be deleted.")
    path = USER_DIR / f"{pid[5:]}.json"
    if path.is_file():
        path.unlink()

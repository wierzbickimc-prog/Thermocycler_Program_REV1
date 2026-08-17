#!/usr/bin/env python3
"""Multi-thermocycler manager.

The Tk app drove exactly one Worker.  Here each unit gets its own Worker (and
its own open serial port), plus a reader thread that folds the Worker's event
queue into a snapshot the web layer can serve or push.

Identity: a real unit is keyed by its USB serial number when the OS exposes one,
so it keeps its nickname across reconnects and re-enumeration; otherwise it falls
back to the port path.  Nicknames live in ~/.builtdna/devices.json.
"""

import json
import math
import os
import queue
import threading
import time
from pathlib import Path

import thermocycler_core as core
from thermocycler_core import GCODE, Worker, flatten_profile

REGISTRY = Path.home() / ".builtdna" / "devices.json"
HISTORY_SECONDS = 900

# One simulated instrument by default; raise BUILTDNA_SIMULATORS to populate the
# grid for a demo or for UI work without hardware attached.
try:
    _SIM_COUNT = max(0, int(os.environ.get("BUILTDNA_SIMULATORS", "1")))
except ValueError:
    _SIM_COUNT = 1
SIM_IDS = tuple(f"sim-{n}" for n in range(1, _SIM_COUNT + 1))

# Ports that are never a thermocycler; probing them is slow and rude.
_PORT_DENY = ("bluetooth", "debug-console", "wlan-debug", "airpods")

# ETA assumptions. The block values are the module's documented maximum ramp
# rates; estimates continuously tighten from live temperatures during a run.
# Lid heat-up is slower and has no published ramp guarantee, so use a
# deliberately conservative nominal rate. The simulator's rates match its model.
BLOCK_HEAT_C_PER_S = 4.25
BLOCK_COOL_C_PER_S = 2.0
LID_HEAT_C_PER_S = 1.0
SIM_BLOCK_C_PER_S = 4.0
SIM_LID_C_PER_S = 2.5


def _ramp_seconds(start, target, heat_rate, cool_rate):
    """Approximate seconds to move between temperatures."""
    if start is None or target is None:
        return 0.0
    delta = float(target) - float(start)
    if abs(delta) <= Worker.REACH_TOLERANCE:
        return 0.0
    rate = heat_rate if delta > 0 else cool_rate
    return max(0.0, abs(delta) - Worker.REACH_TOLERANCE) / rate


def estimate_run_remaining(steps, index=0, phase=None, step_remaining=None,
                           block_current=None, lid_current=None,
                           lid_target=None, simulated=False):
    """Return ``(seconds, kind)`` for a running flattened profile.

    ``kind`` is ``complete`` for a finite profile, ``final_hold`` when the
    estimate ends upon reaching a terminal indefinite hold, or ``indefinite``
    when an indefinite hold prevents a completion estimate.
    """
    if not steps:
        return None, None

    idx = max(0, min(int(index), len(steps) - 1))
    block_heat = SIM_BLOCK_C_PER_S if simulated else BLOCK_HEAT_C_PER_S
    block_cool = SIM_BLOCK_C_PER_S if simulated else BLOCK_COOL_C_PER_S
    lid_heat = SIM_LID_C_PER_S if simulated else LID_HEAT_C_PER_S
    total = 0.0

    def add_step(step, start_temp, remaining_override=None):
        ramp = _ramp_seconds(start_temp, step["temp"], block_heat, block_cool)
        seconds = step.get("seconds")
        if seconds is None or seconds <= 0:
            return ramp, False
        dwell = seconds if remaining_override is None else max(0, remaining_override)
        return ramp + dwell, True

    if phase == "preheat":
        total += _ramp_seconds(lid_current, lid_target, lid_heat, lid_heat)
        next_idx = idx
        previous_temp = block_current
    else:
        current = steps[idx]
        if phase == "hold_inf":
            return ((0, "final_hold") if idx == len(steps) - 1
                    else (None, "indefinite"))
        if phase == "hold":
            seconds = current.get("seconds")
            if seconds is None or seconds <= 0:
                return ((0, "final_hold") if idx == len(steps) - 1
                        else (None, "indefinite"))
            total += max(0, step_remaining if step_remaining is not None else seconds)
        else:
            duration, finite = add_step(current, block_current)
            total += duration
            if not finite:
                return ((math.ceil(total), "final_hold") if idx == len(steps) - 1
                        else (None, "indefinite"))
        previous_temp = current["temp"]
        next_idx = idx + 1

    kind = "complete"
    for n in range(next_idx, len(steps)):
        step = steps[n]
        duration, finite = add_step(step, previous_temp)
        total += duration
        if not finite:
            if n == len(steps) - 1:
                kind = "final_hold"
                break
            return None, "indefinite"
        previous_temp = step["temp"]

    return math.ceil(total), kind


def _is_candidate(device, description):
    text = f"{device} {description}".lower()
    return not any(bad in text for bad in _PORT_DENY)


# ---------------------------------------------------------------------------
#  Nickname registry
# ---------------------------------------------------------------------------
def _load_registry():
    try:
        with REGISTRY.open() as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_registry(reg):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix(".tmp")
    with tmp.open("w") as f:
        json.dump(reg, f, indent=2)
    tmp.replace(REGISTRY)


# ---------------------------------------------------------------------------
#  One managed unit
# ---------------------------------------------------------------------------
class Device:
    """A Worker plus the latest state derived from its event stream."""

    def __init__(self, dev_id, name, port, simulated, on_change):
        self.id = dev_id
        self.name = name
        self.port = port
        self.simulated = simulated
        self._on_change = on_change

        self.out_q = queue.Queue()
        self.worker = Worker(self.out_q)
        self.worker.start()

        self._lock = threading.Lock()
        self.connected = False
        self.block_current = self.block_target = None
        self.lid_current = self.lid_target = None
        self.lid_status = "unknown"
        self.lid_moving_to = None        # inferred from the last open/close
        self.running = False
        self.run_label = "Idle"
        self.run_phase = None
        self.step_index = 0
        self.step_total = 0
        self.remaining = None
        self.run_remaining_s = None
        self.run_completion_kind = None
        self._profile_steps = []
        self._profile_lid_target = None
        self.history = []                # [(t, block, lid)]
        self.log = []                    # last N log lines
        self.profile_name = None
        self.error = None

        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    # -- event stream ------------------------------------------------------
    def _drain(self):
        while True:
            try:
                msg = self.out_q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._apply(msg)
            except Exception:
                pass
            self._on_change(self.id)

    def _apply(self, msg):
        kind = msg["kind"]
        with self._lock:
            if kind == "telemetry":
                self.block_current = msg["block_current"]
                self.block_target = msg["block_target"]
                self.lid_current = msg["lid_current"]
                self.lid_target = msg["lid_target"]
                status = msg["lid_status"]
                if status != "in_between":
                    self.lid_moving_to = None
                self.lid_status = status
                self.history.append((msg["t"], msg["block_current"],
                                     msg["lid_current"]))
                cutoff = msg["t"] - HISTORY_SECONDS
                while self.history and self.history[0][0] < cutoff:
                    self.history.pop(0)
            elif kind == "connected":
                self.connected = True
                self.error = None
            elif kind == "disconnected":
                self.connected = False
                self.running = False
                self.run_label = "Idle"
                self.run_remaining_s = None
                self.run_completion_kind = None
                self._profile_steps = []
                self._profile_lid_target = None
            elif kind == "connect_failed":
                self.connected = False
                self.error = msg.get("text")
            elif kind == "error":
                self.error = msg.get("text")
            elif kind == "run_started":
                self.running = True
                self.step_total = msg["total"]
                self.step_index = 0
                self.run_phase = None
                self.remaining = None
                self.run_label = "Starting"
            elif kind == "run_step":
                self.run_phase = msg["phase"]
                self.step_index = msg["index"]
                self.remaining = msg.get("remaining")
                self.run_label = self._step_text(msg)
            elif kind == "run_finished":
                self.running = False
                self.run_phase = None
                self.remaining = None
                self.run_remaining_s = None
                self.run_completion_kind = None
                self._profile_steps = []
                self._profile_lid_target = None
                self.run_label = "Stopped" if msg.get("aborted") else "Complete"
            elif kind == "log":
                self.log.append({"t": time.time(), "text": msg["text"],
                                 "dir": msg.get("direction", "info")})
                del self.log[:-400]

    def _step_text(self, msg):
        phase = msg["phase"]
        if phase == "preheat":
            return f"Preheating lid to {msg['temp']:.0f}°C"
        n, total = msg["index"] + 1, self.step_total
        label = msg["label"]
        if phase == "ramp":
            return f"{n}/{total} {label} — ramping to {msg['temp']:.0f}°C"
        if phase == "hold_inf":
            return f"{n}/{total} {label} — holding {msg['temp']:.0f}°C"
        rem = msg.get("remaining")
        tail = f" — {rem}s left" if rem is not None else ""
        return f"{n}/{total} {label} — {msg['temp']:.0f}°C{tail}"

    # -- commands ----------------------------------------------------------
    def connect(self):
        self.worker.submit("connect", port=self.port, simulate=self.simulated)

    def disconnect(self):
        self.worker.submit("disconnect")

    def action(self, name, **kw):
        if name in ("open_lid", "close_lid"):
            with self._lock:
                self.lid_moving_to = "open" if name == "open_lid" else "closed"
        self.worker.submit(name, **kw)

    def run_profile(self, profile):
        steps = flatten_profile(profile["stages"])
        if not steps:
            raise ValueError("Profile has no steps.")
        with self._lock:
            self.profile_name = profile.get("name")
            self._profile_steps = list(steps)
            self._profile_lid_target = (profile.get("lid_temp")
                                        if profile.get("preheat_lid", True) else None)
        self.worker.submit("run_profile", steps=steps,
                           volume=profile.get("volume"),
                           lid_temp=profile.get("lid_temp"),
                           preheat_lid=profile.get("preheat_lid", True))

    def shutdown(self):
        self.worker.submit("disconnect")
        self.worker.shutdown()
        self.worker.join(timeout=2.0)

    # -- serialisation -----------------------------------------------------
    def snapshot(self, with_history=False, with_log=False):
        with self._lock:
            if self.running:
                self.run_remaining_s, self.run_completion_kind = estimate_run_remaining(
                    self._profile_steps, index=self.step_index,
                    phase=self.run_phase, step_remaining=self.remaining,
                    block_current=self.block_current, lid_current=self.lid_current,
                    lid_target=self._profile_lid_target, simulated=self.simulated)
            completion_at = (time.time() + self.run_remaining_s
                             if self.run_remaining_s is not None else None)
            data = {
                "id": self.id, "name": self.name, "port": self.port,
                "simulated": self.simulated, "connected": self.connected,
                "block_current": self.block_current,
                "block_target": self.block_target,
                "lid_current": self.lid_current,
                "lid_target": self.lid_target,
                "lid_status": self.lid_status,
                "lid_moving_to": self.lid_moving_to,
                "running": self.running, "run_label": self.run_label,
                "run_phase": self.run_phase,
                "step_index": self.step_index, "step_total": self.step_total,
                "remaining": self.remaining,
                "run_remaining_s": self.run_remaining_s,
                "run_completion_at": completion_at,
                "run_completion_kind": self.run_completion_kind,
                "profile_name": self.profile_name,
                "error": self.error,
            }
            if with_history:
                data["history"] = [
                    {"t": t, "block": b, "lid": l} for t, b, l in self.history]
            if with_log:
                data["log"] = list(self.log[-200:])
        return data


# ---------------------------------------------------------------------------
#  Manager
# ---------------------------------------------------------------------------
class DeviceManager:
    def __init__(self):
        self._devices = {}
        self._lock = threading.RLock()
        self._registry = _load_registry()
        self._subscribers = []
        self._sub_lock = threading.Lock()
        self._add_simulators()

    # -- change fan-out ----------------------------------------------------
    def subscribe(self):
        q = queue.Queue(maxsize=64)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._sub_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _changed(self, dev_id):
        with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(dev_id)
            except queue.Full:
                pass                      # slow client: it will catch up on the
                                          # next event it does receive

    # -- inventory ---------------------------------------------------------
    def _add_simulators(self):
        for n, sid in enumerate(SIM_IDS, 1):
            name = self._registry.get(sid, {}).get(
                "name", "Simulator" if len(SIM_IDS) == 1 else f"Simulator {n}")
            dev = Device(sid, name, None, True, self._changed)
            self._devices[sid] = dev
            dev.connect()

    def devices(self):
        with self._lock:
            return [self._devices[k] for k in sorted(self._devices)]

    def get(self, dev_id):
        with self._lock:
            if dev_id not in self._devices:
                raise KeyError(dev_id)
            return self._devices[dev_id]

    def rename(self, dev_id, name):
        dev = self.get(dev_id)
        name = (name or "").strip() or dev.id
        dev.name = name
        entry = self._registry.setdefault(dev_id, {})
        entry["name"] = name
        _save_registry(self._registry)
        self._changed(dev_id)
        return dev

    # -- discovery ---------------------------------------------------------
    def scan(self):
        """Probe the serial ports and add any thermocycler that answers M119."""
        if not core.HAVE_SERIAL:
            return []
        found = []
        for port in core.list_ports.comports():
            if not _is_candidate(port.device, port.description or ""):
                continue
            dev_id = f"sn:{port.serial_number}" if getattr(
                port, "serial_number", None) else f"port:{port.device}"
            with self._lock:
                existing = self._devices.get(dev_id)
            if existing is not None:
                existing.port = port.device
                found.append(dev_id)
                continue
            if not self._probe(port.device):
                continue
            name = self._registry.get(dev_id, {}).get(
                "name", port.description or port.device)
            dev = Device(dev_id, name, port.device, False, self._changed)
            with self._lock:
                self._devices[dev_id] = dev
            dev.connect()
            found.append(dev_id)
        self._changed("*")
        return found

    @staticmethod
    def _probe(port):
        """True if something on this port answers the lid-status query."""
        try:
            transport = core.SerialTransport(port, timeout=1.5)
        except Exception:
            return False
        try:
            reply = transport.send(GCODE["get_lid_status"], timeout=1.5)
            return "lid" in (reply or "").lower()
        except Exception:
            return False
        finally:
            transport.close()

    def shutdown(self):
        for dev in self.devices():
            try:
                dev.shutdown()
            except Exception:
                pass

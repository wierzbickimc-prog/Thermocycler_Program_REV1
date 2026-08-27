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
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import thermocycler_core as core
from thermocycler_core import GCODE, Worker, flatten_profile
from run_history import RunStore, render_run_pdf

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


def _filename_part(text, maxlen=40):
    """A console-safe fragment for a run-report filename."""
    clean = re.sub(r'[\\/:*?"<>|\s]+', "-", str(text).strip()).strip("-.")
    return clean[:maxlen]


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

    def __init__(self, dev_id, name, port, simulated, on_change, run_store,
                 generation=None):
        self.id = dev_id
        self.name = name
        self.port = port
        self.simulated = simulated
        self.generation = generation
        self.supports_plate_lift = generation == 2
        self._on_change = on_change
        self._runs = run_store

        self.out_q = queue.Queue()
        self.worker = Worker(self.out_q)
        self.worker.start()

        self._lock = threading.Lock()
        self.connected = False
        self.connecting = False
        self._auto_reconnect = True
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
        self._run_id = None
        self._run_lab_id = None
        self._last_run_id = None
        self._resume_record = self._runs.latest_resumable(self.id)
        latest = self._runs.latest_run(self.id)
        self._recovery_notice = self._recovery_from_run(latest)
        self._auto_resume_pending = False
        self._telemetry_since_connect = False
        if latest:
            self._last_run_id = latest["id"]
            if self._resume_record:
                self.profile_name = self._resume_record.get("profile_name")
                self.run_label = "Interrupted — ready to resume"
        self._last_progress_key = None

        self._reader_stopping = threading.Event()
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    # -- event stream ------------------------------------------------------
    def _drain(self):
        while not self._reader_stopping.is_set() or not self.out_q.empty():
            try:
                msg = self.out_q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._apply(msg)
                self._maybe_auto_resume()
            except Exception as exc:
                # A full/unwritable disk must be visible to the operator; an
                # apparently healthy run with no durable journal is unsafe.
                with self._lock:
                    self.error = f"Run logging error: {exc}"
            self._on_change(self.id)

    def _apply(self, msg):
        kind = msg["kind"]
        with self._lock:
            if kind == "telemetry":
                self._telemetry_since_connect = True
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
                if self._run_id:
                    self._runs.add_telemetry(self._run_id, msg)
            elif kind == "connected":
                self.connected = True
                self.connecting = False
                self.error = None
                self._telemetry_since_connect = False
                self._auto_resume_pending = (
                    self._auto_reconnect and self._resume_record is not None)
                if self._auto_resume_pending:
                    self._runs.add_event(
                        self._resume_record["id"], "connection_restored",
                        "Communication restored; validating telemetry before automatic resume")
            elif kind == "disconnected":
                self.connected = False
                self.connecting = False
                self.running = False
                if msg.get("unexpected"):
                    self.error = msg.get("reason") or "Communication lost"
                    self._auto_reconnect = True
                self.run_label = ("Interrupted — ready to resume"
                                  if self._resume_record else "Idle")
                self.run_remaining_s = None
                self.run_completion_kind = None
                self._auto_resume_pending = False
                self._profile_steps = []
                self._profile_lid_target = None
            elif kind == "connect_failed":
                self.connected = False
                self.connecting = False
                self.error = msg.get("text")
            elif kind == "error":
                self.error = msg.get("text")
            elif kind == "run_started":
                self.running = True
                self.step_total = msg["total"]
                self.step_index = msg.get("index", 0)
                self.run_phase = None
                self.remaining = None
                self.run_label = "Resuming" if msg.get("resumed") else "Starting"
            elif kind == "run_step":
                self.run_phase = msg["phase"]
                self.step_index = msg["index"]
                self.remaining = msg.get("remaining")
                self.run_label = self._step_text(msg)
                self._runs.update_progress(
                    self._run_id, self.step_index, self.step_total,
                    self.run_phase, self.remaining)
                progress_key = (self.step_index, self.run_phase)
                if progress_key != self._last_progress_key:
                    self._runs.add_event(
                        self._run_id, "progress", self.run_label,
                        {"step_index": self.step_index, "phase": self.run_phase})
                    self._last_progress_key = progress_key
            elif kind == "run_interrupted":
                self.step_index = msg.get("index", self.step_index)
                self.run_phase = msg.get("phase")
                self.remaining = msg.get("remaining")
                self._runs.update_progress(
                    self._run_id, self.step_index, self.step_total,
                    self.run_phase, self.remaining)
                self._runs.interrupt(self._run_id,
                                     msg.get("reason") or "Communication interrupted")
                self.running = False
                self.run_remaining_s = None
                self.run_completion_kind = None
                self.run_label = "Interrupted — ready to resume"
                self._resume_record = self._runs.get_run(self._run_id)
                self._recovery_notice = self._recovery_from_run(self._resume_record)
                self._last_run_id = self._run_id
            elif kind == "run_finished":
                self.running = False
                self.run_phase = None
                self.remaining = None
                self.run_remaining_s = None
                self.run_completion_kind = None
                self._profile_steps = []
                self._profile_lid_target = None
                self.run_label = "Stopped" if msg.get("aborted") else "Complete"
                status = "stopped" if msg.get("aborted") else "completed"
                self._runs.finish(self._run_id, status)
                self._last_run_id = self._run_id
                self._recovery_notice = self._recovery_from_run(
                    self._runs.get_run(self._run_id))
                self._run_id = None
                self._resume_record = None
                self._last_progress_key = None
            elif kind == "log":
                self.log.append({"t": time.time(), "text": msg["text"],
                                 "dir": msg.get("direction", "info")})
                del self.log[:-400]
                self._runs.add_event(self._run_id, "log", msg["text"],
                                     {"direction": msg.get("direction", "info")})

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

    @staticmethod
    def _recovery_from_run(record):
        if not record:
            return None
        lost_at = record.get("interrupted_at")
        if not lost_at:
            return None
        steps = record.get("steps") or []
        interrupted_index = record.get("interrupted_step_index")
        raw_index = (record.get("step_index", 0)
                     if interrupted_index is None else interrupted_index)
        index = max(0, min(int(raw_index), len(steps) - 1)) if steps else 0
        step = steps[index] if steps else {}
        return {
            "run_id": record["id"],
            "lost_at": lost_at,
            "resumed_at": record.get("resumed_at"),
            "automatic": bool(record.get("resume_automatic")),
            "cycle": step.get("cycle"),
            "cycles": step.get("cycles"),
            "step_index": index,
            "step_total": record.get("step_total", len(steps)),
            "stage_name": step.get("stage_name") or step.get("label"),
            "reason": record.get("interruption_reason"),
        }

    def _maybe_auto_resume(self):
        """Resume only after this connection has produced valid telemetry."""
        move_action = None
        should_resume = False
        with self._lock:
            if not (self._auto_resume_pending and self.connected
                    and self._telemetry_since_connect and not self.running
                    and self._resume_record):
                return
            required = self._resume_record.get("profile", {}).get(
                "lid_position", "closed")
            if self.lid_status in ("unknown", "in_between"):
                return
            if self.lid_status != required:
                if self.lid_moving_to != required:
                    move_action = "close_lid" if required == "closed" else "open_lid"
            else:
                self._auto_resume_pending = False
                should_resume = True
        if move_action:
            self.action(move_action)
        elif should_resume:
            try:
                self.resume_run(automatic=True)
            except Exception as exc:
                with self._lock:
                    self.error = f"Automatic resume failed: {exc}"

    # -- commands ----------------------------------------------------------
    def connect(self):
        with self._lock:
            if self.connected or self.connecting:
                return
            self.connecting = True
            self._auto_reconnect = True
        self.worker.submit("connect", port=self.port, simulate=self.simulated)

    def disconnect(self):
        with self._lock:
            self._auto_reconnect = False
        self.worker.submit("disconnect")

    def action(self, name, **kw):
        if name == "plate_lift":
            if not self.supports_plate_lift:
                raise ValueError(
                    "Plate lift is only available on GEN2 thermocyclers.")
            with self._lock:
                lid_status = self.lid_status
            if lid_status != "open":
                raise ValueError(
                    "Plate lift cannot be performed unless the lid is fully open "
                    f"(current lid status: {lid_status}).")
        if name in ("open_lid", "close_lid"):
            with self._lock:
                self.lid_moving_to = "open" if name == "open_lid" else "closed"
        self.worker.submit(name, **kw)

    def run_profile(self, profile, lab_id=None):
        steps = flatten_profile(profile["stages"])
        if not steps:
            raise ValueError("Profile has no steps.")
        with self._lock:
            if not self.connected:
                raise ValueError("Instrument is not connected.")
            if self.running:
                raise ValueError("Instrument is already running a profile.")
            self.profile_name = profile.get("name")
            self._run_lab_id = (str(lab_id).strip() if lab_id else "") or None
            self._profile_steps = list(steps)
            self._profile_lid_target = (profile.get("lid_temp")
                                        if profile.get("preheat_lid", True) else None)
            self._run_id = self._runs.start_run(
                self.id, self.name, self.simulated, profile, steps,
                lab_id=self._run_lab_id)
            self._last_run_id = self._run_id
            self._resume_record = None
            self._recovery_notice = None
            self._auto_resume_pending = False
            self._last_progress_key = None
        self.worker.submit("run_profile", steps=steps,
                           volume=profile.get("volume"),
                           lid_temp=profile.get("lid_temp"),
                           preheat_lid=profile.get("preheat_lid", True))

    def resume_run(self, automatic=False):
        with self._lock:
            if not self.connected:
                raise ValueError("Reconnect the instrument before resuming.")
            if self.running:
                raise ValueError("The instrument is already running.")
            record = self._runs.latest_resumable(self.id)
            if not record:
                raise ValueError("There is no interrupted run to resume.")
            self._run_lab_id = record.get("lab_id")
            profile = record["profile"]
            steps = record["steps"]
            required = profile.get("lid_position", "closed")
            if self.lid_status != required:
                raise ValueError(
                    f"Profile requires the lid {required}; current lid is "
                    f"{self.lid_status}.")
            self._run_id = record["id"]
            self._last_run_id = self._run_id
            self.profile_name = record.get("profile_name")
            self._profile_steps = list(steps)
            self._profile_lid_target = (profile.get("lid_temp")
                                        if profile.get("preheat_lid", True) else None)
            self.step_total = len(steps)
            self._runs.resume_run(self._run_id, automatic=automatic)
            resumed_record = self._runs.get_run(self._run_id)
            self._recovery_notice = self._recovery_from_run(resumed_record)
            self._resume_record = None
            self._auto_resume_pending = False
            self._last_progress_key = None
            self.worker.submit(
                "run_profile", steps=steps, volume=profile.get("volume"),
                lid_temp=profile.get("lid_temp"),
                preheat_lid=profile.get("preheat_lid", True),
                resume_index=record.get("step_index", 0),
                resume_phase=record.get("phase"),
                resume_remaining=record.get("remaining_s"), resume=True)

    def run_report_pdf(self):
        with self._lock:
            run_id = self._last_run_id
        if not run_id:
            raise ValueError("No run has been recorded for this instrument.")
        run = self._runs.get_run(run_id, details=True)
        if not run:
            raise ValueError("The recorded run could not be found.")
        return render_run_pdf(run)

    def shutdown(self):
        self.worker.submit("disconnect")
        self.worker.shutdown()
        self.worker.join(timeout=7.0)
        self._reader_stopping.set()
        self._reader.join(timeout=2.0)

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
                "connecting": self.connecting,
                "generation": self.generation,
                "supports_plate_lift": self.supports_plate_lift,
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
                "lab_id": self._run_lab_id,
                "error": self.error,
                "resume_available": self._resume_record is not None,
                "resume": ({
                    "run_id": self._resume_record["id"],
                    "profile_name": self._resume_record.get("profile_name"),
                    "step_index": self._resume_record.get("step_index", 0),
                    "step_total": self._resume_record.get("step_total", 0),
                    "phase": self._resume_record.get("phase"),
                    "remaining": self._resume_record.get("remaining_s"),
                    "lid_position": self._resume_record.get("profile", {}).get(
                        "lid_position", "closed"),
                    "interrupted_at": self._resume_record.get("interrupted_at"),
                    "cycle": ((self._resume_record.get("steps") or [{}])[
                        min(self._resume_record.get("step_index", 0),
                            len(self._resume_record.get("steps") or [{}]) - 1)
                    ].get("cycle")),
                    "cycles": ((self._resume_record.get("steps") or [{}])[
                        min(self._resume_record.get("step_index", 0),
                            len(self._resume_record.get("steps") or [{}]) - 1)
                    ].get("cycles")),
                    "reason": self._resume_record.get("interruption_reason"),
                } if self._resume_record else None),
                "latest_run_id": self._last_run_id,
                "auto_resume_pending": self._auto_resume_pending,
                "recovery_notice": (dict(self._recovery_notice)
                                    if self._recovery_notice else None),
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
    def __init__(self, run_store=None):
        self._devices = {}
        self._lock = threading.RLock()
        self._registry = _load_registry()
        self._subscribers = []
        self._sub_lock = threading.Lock()
        self._runs = run_store or RunStore()
        self._scan_lock = threading.Lock()
        self._monitor_stop = threading.Event()
        self._monitor_thread = None
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
            dev = Device(sid, name, None, True, self._changed, self._runs,
                         generation=1)
            self._devices[sid] = dev
            dev.connect()

    def devices(self):
        with self._lock:
            return [self._devices[k] for k in sorted(self._devices)]

    def runs(self, limit=100, q=None):
        return self._runs.list_runs(limit=limit, q=q)

    def run_report(self, run_id):
        """Return ``(pdf_bytes, filename)`` for any recorded run."""
        run = self._runs.get_run(run_id, details=True)
        if not run:
            raise KeyError(run_id)
        stamp = datetime.fromtimestamp(run["started_at"]).strftime(
            "%Y-%m-%d_%H%M%S")
        bits = ["thermocycler-run"]
        if run.get("lab_id"):
            bits.append(_filename_part(run["lab_id"]))
        bits.append(_filename_part(run.get("profile_name") or "profile"))
        bits.append(stamp)
        return render_run_pdf(run), "-".join(bits) + ".pdf"

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
        with self._scan_lock:
            for port in core.list_ports.comports():
                if not _is_candidate(port.device, port.description or ""):
                    continue
                dev_id = f"sn:{port.serial_number}" if getattr(
                    port, "serial_number", None) else f"port:{port.device}"
                with self._lock:
                    existing = self._devices.get(dev_id)
                if existing is not None:
                    existing.port = port.device
                    generation = core.thermocycler_generation(
                        usb_pid=getattr(port, "pid", None))
                    if generation is not None:
                        existing.generation = generation
                        existing.supports_plate_lift = generation == 2
                    if (not existing.connected and not existing.connecting
                            and existing._auto_reconnect):
                        existing.connect()
                    found.append(dev_id)
                    continue
                probe = self._probe(port.device, getattr(port, "pid", None))
                if probe is None:
                    continue
                name = self._registry.get(dev_id, {}).get(
                    "name", port.description or port.device)
                dev = Device(dev_id, name, port.device, False, self._changed,
                             self._runs, generation=probe["generation"])
                with self._lock:
                    self._devices[dev_id] = dev
                dev.connect()
                found.append(dev_id)
        self._changed("*")
        return found

    def start_monitor(self, interval=3.0):
        """Watch for a powered-off USB module to reappear, then reconnect only."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        def monitor():
            while not self._monitor_stop.is_set():
                try:
                    self.scan()
                except Exception:
                    pass
                self._monitor_stop.wait(interval)
        self._monitor_thread = threading.Thread(target=monitor, daemon=True)
        self._monitor_thread.start()

    @staticmethod
    def _probe(port, usb_pid=None):
        """Return discovered capabilities if the port is a thermocycler."""
        try:
            transport = core.SerialTransport(port, timeout=1.5)
        except Exception:
            return None
        try:
            reply = transport.send(GCODE["get_lid_status"], timeout=1.5)
            if "lid" not in (reply or "").lower():
                return None
            device_info = transport.send(GCODE["get_device_info"], timeout=1.5)
            return {
                "generation": core.thermocycler_generation(
                    device_info, usb_pid),
                "device_info": device_info,
            }
        except Exception:
            return None
        finally:
            transport.close()

    def shutdown(self):
        self._monitor_stop.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)
        for dev in self.devices():
            try:
                dev.shutdown()
            except Exception:
                pass
        self._runs.close()

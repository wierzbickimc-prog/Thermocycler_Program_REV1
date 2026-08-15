#!/usr/bin/env python3
"""
Opentrons Thermocycler Module GEN 1 - core protocol + control logic.

This module contains everything that talks to the hardware (or the built-in
simulator) and has NO GUI dependency, so it can be imported and unit-tested
on its own, reused from a script, or driven from a different front-end.

Serial protocol (GEN 1) - verified against the Opentrons driver source
----------------------------------------------------------------------
  Baud rate ............ 115200
  Line terminator ...... "\\r\\n"
  Acknowledgement ...... two "ok" lines ("ok\\r\\nok\\r\\n")
  G-codes:
    M104  set block/plate temp     (S<temp> [H<hold_s>] [V<volume_uL>])
    M105  get block/plate temp     -> "T:<target> C:<current> H:<hold>"
    M140  set lid temp             (S<temp>)
    M141  get lid temp             -> "T:<target> C:<current>"
    M119  get lid status           -> "Lid:<open|closed|in_between>"
    M126  open lid
    M127  close lid
    M128  plate lift
    M14   deactivate block
    M108  deactivate lid
    M18   deactivate all
"""

import math
import queue
import re
import threading
import time

try:
    import serial
    import serial.tools.list_ports as list_ports
    HAVE_SERIAL = True
except Exception:  # pragma: no cover - serial simply unavailable
    HAVE_SERIAL = False
    list_ports = None


# ============================================================================
#  Protocol constants
# ============================================================================
BAUD_RATE = 115200
TERMINATOR = "\r\n"

GCODE = {
    "set_block": "M104",
    "get_block": "M105",
    "set_lid": "M140",
    "get_lid": "M141",
    "get_lid_status": "M119",
    "open_lid": "M126",
    "close_lid": "M127",
    "plate_lift": "M128",
    "deactivate_block": "M14",
    "deactivate_lid": "M108",
    "deactivate_all": "M18",
}

# Physical limits for the GEN 1 module (used for input validation only).
BLOCK_MIN_C, BLOCK_MAX_C = 4.0, 99.0
LID_MIN_C, LID_MAX_C = 37.0, 110.0


# ============================================================================
#  Pure helpers - command building & response parsing (unit-testable)
# ============================================================================
def build_set_block(temp, hold_s=None, volume_uL=None):
    """Build the M104 command string for a block-temperature set."""
    cmd = f"{GCODE['set_block']} S{float(temp):.2f}"
    if hold_s is not None:
        cmd += f" H{int(round(float(hold_s)))}"
    if volume_uL is not None:
        cmd += f" V{float(volume_uL):.1f}"
    return cmd


def build_set_lid(temp):
    """Build the M140 command string for a lid-temperature set."""
    return f"{GCODE['set_lid']} S{float(temp):.2f}"


def parse_key_values(payload):
    """Parse 'T:none C:25.0 H:123' style responses into a dict of strings."""
    result = {}
    if not payload:
        return result
    for m in re.finditer(
        r"([A-Za-z_]+)\s*:\s*"
        r"(-?\d+(?:\.\d+)?|none|off|open|closed|in_between|error|unknown)",
        payload,
        flags=re.IGNORECASE,
    ):
        result[m.group(1)] = m.group(2)
    return result


def as_float(value):
    """Convert a parsed field to float, or None for none/off/blank."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def parse_temperature(payload):
    """Return (current, target) from an M105/M141 response payload."""
    kv = parse_key_values(payload)
    return as_float(kv.get("C")), as_float(kv.get("T"))


def parse_lid_status(payload):
    """Return the lid status ('open'/'closed'/'in_between'/'unknown')."""
    kv = parse_key_values(payload)
    for key in ("Lid", "lid", "LID"):
        if key in kv:
            return kv[key].lower()
    return "unknown"


def available_ports():
    """Return a list of (device, description) tuples for connected serial ports."""
    if not HAVE_SERIAL:
        return []
    return [(p.device, p.description) for p in list_ports.comports()]


# ============================================================================
#  Serial transport
# ============================================================================
class SerialTransport:
    """Thin wrapper around pyserial implementing the GEN 1 line protocol."""

    def __init__(self, port, baud=BAUD_RATE, timeout=5.0):
        if not HAVE_SERIAL:
            raise RuntimeError("pyserial is not installed.  Run: pip install pyserial")
        self.port = port
        self._ser = serial.Serial(port, baud, timeout=0.2)
        self._timeout = timeout
        time.sleep(1.0)  # allow the module's serial interface to settle
        try:
            self._ser.reset_input_buffer()
        except Exception:
            pass

    def send(self, command, timeout=None):
        """Send a command and return the response payload (ack lines stripped)."""
        timeout = self._timeout if timeout is None else timeout
        line = command.strip() + TERMINATOR
        self._ser.reset_input_buffer()
        self._ser.write(line.encode("ascii"))
        self._ser.flush()

        buf = ""
        ok_count = 0
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = self._ser.read(256).decode("ascii", errors="ignore")
            if chunk:
                buf += chunk
                ok_count = len(re.findall(r"(?mi)^ok\s*$", buf))
                if ok_count >= 2:
                    break
            elif ok_count >= 1:
                break  # some builds emit a single 'ok'; accept after a gap
        lines = [ln.strip() for ln in re.split(r"[\r\n]+", buf) if ln.strip()]
        data = [ln for ln in lines if ln.lower() != "ok"]
        return " ".join(data)

    def close(self):
        try:
            self._ser.close()
        except Exception:
            pass


# ============================================================================
#  Simulator transport (lets the whole app run with no hardware attached)
# ============================================================================
class SimulatorTransport:
    """Software model of a GEN 1 thermocycler for demos and UI testing."""

    def __init__(self):
        self.block_current = 23.0
        self.block_target = None
        self.block_hold = None
        self.lid_current = 23.0
        self.lid_target = None
        self.lid_status = "closed"
        self._lid_pending = "closed"
        self._lid_move_done = 0.0
        self._last = time.time()

    def _advance(self):
        now = time.time()
        dt = now - self._last
        self._last = now
        self._ramp("block_current", self.block_target, 4.0 * dt)
        self._ramp("lid_current", self.lid_target, 2.5 * dt)
        if self.lid_status == "in_between" and now >= self._lid_move_done:
            self.lid_status = self._lid_pending

    def _ramp(self, attr, target, step):
        cur = getattr(self, attr)
        goal = target if target is not None else 23.0
        if step <= 0 or abs(goal - cur) <= step:
            setattr(self, attr, goal)
        else:
            setattr(self, attr, cur + math.copysign(step, goal - cur))

    def _fmt(self, target):
        return "none" if target is None else f"{target:.2f}"

    def send(self, command, timeout=None):
        self._advance()
        parts = command.strip().split()
        code = parts[0] if parts else ""
        args = {p[0]: p[1:] for p in parts[1:] if p}

        if code == GCODE["set_block"]:
            self.block_target = float(args.get("S", self.block_target or 23.0))
            self.block_hold = float(args["H"]) if "H" in args else None
        elif code == GCODE["set_lid"]:
            self.lid_target = float(args.get("S", self.lid_target or 23.0))
        elif code == GCODE["get_block"]:
            return (f"T:{self._fmt(self.block_target)} "
                    f"C:{self.block_current:.2f} "
                    f"H:{self._fmt(self.block_hold)}")
        elif code == GCODE["get_lid"]:
            return f"T:{self._fmt(self.lid_target)} C:{self.lid_current:.2f}"
        elif code == GCODE["get_lid_status"]:
            return f"Lid:{self.lid_status}"
        elif code in (GCODE["open_lid"], GCODE["close_lid"]):
            self._lid_pending = "open" if code == GCODE["open_lid"] else "closed"
            self.lid_status = "in_between"
            self._lid_move_done = time.time() + 2.0
        elif code == GCODE["plate_lift"]:
            pass
        elif code == GCODE["deactivate_block"]:
            self.block_target = self.block_hold = None
        elif code == GCODE["deactivate_lid"]:
            self.lid_target = None
        elif code == GCODE["deactivate_all"]:
            self.block_target = self.lid_target = self.block_hold = None
        return ""

    def close(self):
        pass


# ============================================================================
#  Profile model helpers
# ============================================================================
def flatten_profile(stages):
    """Expand [{name, cycles, steps:[{temp, seconds}]}] into a linear step list."""
    flat = []
    for stage in stages:
        cycles = max(1, int(stage.get("cycles", 1)))
        for c in range(cycles):
            for st in stage["steps"]:
                label = stage["name"]
                if cycles > 1:
                    label += f"  (cycle {c + 1}/{cycles})"
                flat.append({
                    "label": label,
                    "temp": float(st["temp"]),
                    "seconds": (None if st.get("seconds") in (None, "", 0, "0")
                                else int(st["seconds"])),
                })
    return flat


DEFAULT_PROFILE = {
    "lid_temp": 105.0,
    "preheat_lid": True,
    "volume": 25.0,
    "stages": [
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
         "steps": [{"temp": 4.0, "seconds": 0}]},  # 0 => hold indefinitely
    ],
}


# ============================================================================
#  Background worker - owns the transport, polls telemetry, runs profiles
# ============================================================================
class Worker(threading.Thread):
    """Single background thread that serialises all device access.

    The GUI (or any caller) submits actions with ``submit()`` and reads
    telemetry / log / status events off the ``out_queue`` passed to __init__.
    """

    POLL_INTERVAL = 1.5       # seconds between telemetry polls
    RUN_POLL_INTERVAL = 0.5   # max age of a temperature reading used by the runner
    REACH_TOLERANCE = 1.0     # +/- C considered "at temperature" (block)
    LID_TOLERANCE = 2.0       # C below target still counts as a preheated lid
    RAMP_TIMEOUT = 600        # safety: max seconds to wait for a ramp
    PREHEAT_TIMEOUT = 900     # safety: max seconds to wait for the lid to preheat

    def __init__(self, out_queue):
        super().__init__(daemon=True)
        self.cmd_q = queue.Queue()
        self.out_q = out_queue
        self._transport = None
        # NB: not '_stop' - that name shadows threading.Thread._stop(), which
        # Thread.join() calls internally, making any join() raise TypeError.
        self._stopping = threading.Event()
        self._last_poll = 0.0

        # Cached temperatures, so the runner does not re-read what the
        # telemetry poll just fetched (see _block_temp / _lid_temp).
        self._block_current = None
        self._block_at = 0.0
        self._lid_current = None
        self._lid_at = 0.0

        # profile run state
        self._run_steps = None
        self._run_idx = 0
        self._phase = None            # None | 'preheat' | 'ramp' | 'hold'
        self._hold_end = None
        self._ramp_deadline = 0.0
        self._run_volume = None
        self._preheat_target = None
        self._preheat_deadline = 0.0
        self._last_remaining = None   # throttles the hold countdown emit to 1 Hz

    # -- public API (thread-safe: just enqueues) ---------------------------
    def submit(self, action, **kw):
        self.cmd_q.put((action, kw))

    def shutdown(self):
        self._stopping.set()

    # -- helpers -----------------------------------------------------------
    def _emit(self, kind, **data):
        data["kind"] = kind
        self.out_q.put(data)

    def _log(self, text, direction="info"):
        self._emit("log", text=text, direction=direction)

    def _send(self, command, quiet=False):
        if not self._transport:
            return ""
        try:
            resp = self._transport.send(command)
            if not quiet:
                self._log(command, "tx")
                if resp:
                    self._log(resp, "rx")
            return resp
        except Exception as exc:
            self._log(f"ERROR sending '{command}': {exc}", "err")
            self._emit("error", text=str(exc))
            self._disconnect()
            return ""

    def _block_temp(self, max_age):
        """Current block temp, reusing the telemetry cache when it is fresh enough.

        Without this the run loop issued its own M105 on every pass (~10 Hz),
        flooding the serial link on top of the regular telemetry poll.
        """
        if (self._block_current is not None
                and (time.time() - self._block_at) <= max_age):
            return self._block_current
        cur, _ = parse_temperature(self._send(GCODE["get_block"], quiet=True))
        self._block_current, self._block_at = cur, time.time()
        return cur

    def _lid_temp(self, max_age):
        """Current lid temp, reusing the telemetry cache when it is fresh enough."""
        if (self._lid_current is not None
                and (time.time() - self._lid_at) <= max_age):
            return self._lid_current
        cur, _ = parse_temperature(self._send(GCODE["get_lid"], quiet=True))
        self._lid_current, self._lid_at = cur, time.time()
        return cur

    # -- connection --------------------------------------------------------
    def _connect(self, port, simulate):
        try:
            if simulate:
                self._transport = SimulatorTransport()
                self._log("Connected to Simulator (demo mode).", "info")
            else:
                self._transport = SerialTransport(port)
                self._log(f"Connected to {port} @ {BAUD_RATE} baud.", "info")
            self._emit("connected", port="Simulator" if simulate else port)
        except Exception as exc:
            self._log(f"Connection failed: {exc}", "err")
            self._emit("connect_failed", text=str(exc))

    def _disconnect(self):
        # Deliberately does not deactivate: disconnecting while the block holds
        # at 4 C is a legitimate thing to want.  The GUI offers to turn the
        # heaters off on quit instead.
        self._abort_run(silent=True)
        if self._transport:
            self._transport.close()
            self._transport = None
            self._log("Disconnected (module keeps its last commanded state).", "info")
        self._block_current = self._lid_current = None
        self._emit("disconnected")

    # -- profile running ---------------------------------------------------
    def _start_run(self, steps, volume, lid_temp, preheat_lid):
        if not self._transport:
            return
        self._run_steps = steps
        self._run_idx = 0
        self._phase = None
        self._hold_end = None
        self._last_remaining = None
        self._run_volume = volume
        self._log(f"=== Starting profile: {len(steps)} step(s) ===", "info")
        if lid_temp is not None:
            self._send(build_set_lid(lid_temp))
            if preheat_lid:
                # Actually hold the block steps until the lid is hot; previously
                # this only logged a message and started heating immediately.
                self._preheat_target = lid_temp
                self._preheat_deadline = time.time() + self.PREHEAT_TIMEOUT
                self._phase = "preheat"
                self._log(f"Preheating lid to {lid_temp:.1f} C "
                          f"before block steps...", "info")
        self._emit("run_started", total=len(steps))

    def _abort_run(self, silent=False, deactivate=False):
        """Stop a running profile.  ``deactivate`` also turns the heaters off."""
        if self._run_steps is None:
            return                       # nothing running: stay quiet
        self._run_steps = None
        self._phase = None
        self._hold_end = None
        self._last_remaining = None
        if not silent:
            self._log("=== Profile stopped ===", "info")
        if deactivate:
            self._send(GCODE["deactivate_block"])
            self._send(GCODE["deactivate_lid"])
            self._log("Block and lid deactivated.", "info")
        self._emit("run_finished", aborted=True)

    def _enter_hold(self, step):
        """Move a step into its hold phase.  ``seconds`` of None/0 holds forever."""
        secs = step["seconds"]
        self._hold_end = None if (secs is None or secs <= 0) else time.time() + secs
        self._phase = "hold"
        self._last_remaining = None
        self._emit("run_step", index=self._run_idx, label=step["label"],
                   temp=step["temp"],
                   phase=("hold_inf" if self._hold_end is None else "hold"),
                   remaining=(None if self._hold_end is None else secs))

    def _tick_run(self):
        if self._run_steps is None:
            return

        if self._phase == "preheat":
            # A preheat takes tens of seconds; the telemetry cache is precise
            # enough, so this costs no extra serial traffic at all.
            cur = self._lid_temp(self.POLL_INTERVAL)
            if cur is not None and cur >= self._preheat_target - self.LID_TOLERANCE:
                self._log(f"Lid at {cur:.1f} C; starting block steps.", "info")
                self._phase = None
            elif time.time() > self._preheat_deadline:
                self._log("Lid preheat timed out; starting block steps anyway.", "err")
                self._phase = None
            elif self._last_remaining != "preheat":
                self._last_remaining = "preheat"     # emit once, not every tick
                self._emit("run_step", index=self._run_idx, label="Preheating lid",
                           temp=self._preheat_target, phase="preheat", remaining=None)
            return

        step = self._run_steps[self._run_idx]

        if self._phase is None:
            self._send(build_set_block(step["temp"], volume_uL=self._run_volume))
            self._phase = "ramp"
            self._ramp_deadline = time.time() + self.RAMP_TIMEOUT
            self._emit("run_step", index=self._run_idx, label=step["label"],
                       temp=step["temp"], phase="ramp", remaining=None)
            return

        if self._phase == "ramp":
            cur = self._block_temp(self.RUN_POLL_INTERVAL)
            if cur is not None and abs(cur - step["temp"]) <= self.REACH_TOLERANCE:
                self._enter_hold(step)
            elif time.time() > self._ramp_deadline:
                self._log(f"Ramp timeout on step {self._run_idx + 1}; continuing.", "err")
                self._enter_hold(step)
            return

        if self._phase == "hold":
            # Holding needs no temperature read at all - the module maintains
            # the target - so this phase costs nothing on the serial link.
            if self._hold_end is None:
                if self._last_remaining != "inf":
                    self._last_remaining = "inf"
                    self._emit("run_step", index=self._run_idx, label=step["label"],
                               temp=step["temp"], phase="hold_inf", remaining=None)
                return
            remaining = self._hold_end - time.time()
            if remaining <= 0:
                self._run_idx += 1
                self._phase = None
                self._last_remaining = None
                if self._run_idx >= len(self._run_steps):
                    self._log("=== Profile complete ===", "info")
                    self._run_steps = None
                    self._emit("run_finished", aborted=False)
            elif int(remaining) != self._last_remaining:
                self._last_remaining = int(remaining)
                self._emit("run_step", index=self._run_idx, label=step["label"],
                           temp=step["temp"], phase="hold", remaining=int(remaining))

    # -- telemetry ---------------------------------------------------------
    def _poll(self):
        if not self._transport:
            return
        b_cur, b_tgt = parse_temperature(self._send(GCODE["get_block"], quiet=True))
        l_cur, l_tgt = parse_temperature(self._send(GCODE["get_lid"], quiet=True))
        lid = parse_lid_status(self._send(GCODE["get_lid_status"], quiet=True))
        now = time.time()
        self._block_current, self._block_at = b_cur, now
        self._lid_current, self._lid_at = l_cur, now
        self._emit("telemetry", t=time.time(),
                   block_current=b_cur, block_target=b_tgt,
                   lid_current=l_cur, lid_target=l_tgt, lid_status=lid)

    # -- main loop ---------------------------------------------------------
    def run(self):
        while not self._stopping.is_set():
            try:
                action, kw = self.cmd_q.get(timeout=0.1)
                self._handle(action, kw)
            except queue.Empty:
                pass

            if self._transport and (time.time() - self._last_poll) >= self.POLL_INTERVAL:
                self._poll()
                self._last_poll = time.time()

            self._tick_run()

        # Drain anything still queued (e.g. a final deactivate submitted by the
        # GUI as it closes) before dropping the port.
        while True:
            try:
                action, kw = self.cmd_q.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle(action, kw)
            except Exception:
                break

        if self._transport:
            self._transport.close()

    def _handle(self, action, kw):
        if action == "connect":
            self._connect(kw["port"], kw["simulate"])
        elif action == "disconnect":
            self._disconnect()
        elif action == "set_block":
            self._send(build_set_block(kw["temp"], kw.get("hold"), kw.get("volume")))
        elif action == "set_lid":
            self._send(build_set_lid(kw["temp"]))
        elif action == "open_lid":
            self._send(GCODE["open_lid"])
        elif action == "close_lid":
            self._send(GCODE["close_lid"])
        elif action == "plate_lift":
            self._send(GCODE["plate_lift"])
        elif action == "deactivate_block":
            self._send(GCODE["deactivate_block"])
        elif action == "deactivate_lid":
            self._send(GCODE["deactivate_lid"])
        elif action == "deactivate_all":
            self._abort_run(silent=True)
            self._send(GCODE["deactivate_all"])
        elif action == "raw":
            self._send(kw["command"])
        elif action == "run_profile":
            self._start_run(kw["steps"], kw.get("volume"),
                            kw.get("lid_temp"), kw.get("preheat_lid", False))
        elif action == "stop_run":
            self._abort_run(deactivate=True)

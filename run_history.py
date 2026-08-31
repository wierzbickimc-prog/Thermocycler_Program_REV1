#!/usr/bin/env python3
"""Durable run checkpoints, audit history, and dependency-free PDF reports."""

import json
import os
import sqlite3
import textwrap
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path


RUN_DB = Path(os.environ.get(
    "BUILTDNA_RUN_DB", str(Path.home() / ".builtdna" / "runs.sqlite3"))).expanduser()


class RunStore:
    """Small transactional run journal.

    A commit is made for every checkpoint or telemetry sample.  WAL mode keeps
    readers responsive while ``synchronous=FULL`` makes acknowledged writes
    survive an operating-system or laptop power failure as reliably as the
    local filesystem permits.
    """

    def __init__(self, path=RUN_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    simulated INTEGER NOT NULL DEFAULT 0,
                    profile_name TEXT,
                    lab_id TEXT,
                    profile_json TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    ended_at REAL,
                    status TEXT NOT NULL,
                    step_index INTEGER NOT NULL DEFAULT 0,
                    step_total INTEGER NOT NULL DEFAULT 0,
                    phase TEXT,
                    remaining_s INTEGER,
                    interruption_reason TEXT,
                    resume_count INTEGER NOT NULL DEFAULT 0,
                    interrupted_at REAL,
                    interrupted_step_index INTEGER,
                    resumed_at REAL,
                    resume_automatic INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS runs_device_time
                    ON runs(device_id, started_at DESC);
                CREATE TABLE IF NOT EXISTS run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    t REAL NOT NULL,
                    kind TEXT NOT NULL,
                    message TEXT,
                    data_json TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );
                CREATE INDEX IF NOT EXISTS events_run_time
                    ON run_events(run_id, t);
                CREATE TABLE IF NOT EXISTS run_telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    t REAL NOT NULL,
                    block_current REAL,
                    block_target REAL,
                    lid_current REAL,
                    lid_target REAL,
                    lid_status TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );
                CREATE INDEX IF NOT EXISTS telemetry_run_time
                    ON run_telemetry(run_id, t);
            """)
            # Add recovery-notification fields to databases created by an
            # earlier build without discarding any recorded runs.
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(runs)")}
            for name, definition in (
                    ("interrupted_at", "REAL"),
                    ("interrupted_step_index", "INTEGER"),
                    ("resumed_at", "REAL"),
                    ("resume_automatic", "INTEGER NOT NULL DEFAULT 0"),
                    ("lab_id", "TEXT")):
                if name not in columns:
                    self._db.execute(f"ALTER TABLE runs ADD COLUMN {name} {definition}")
            now = time.time()
            stale = [row[0] for row in self._db.execute(
                "SELECT id FROM runs WHERE status='running'")]
            self._db.execute(
                """UPDATE runs SET status='interrupted', updated_at=?,
                       interrupted_at=?, resumed_at=NULL, resume_automatic=0,
                       interrupted_step_index=step_index,
                       interruption_reason=COALESCE(interruption_reason,
                           'Application stopped before the run completed.')
                   WHERE status='running'""", (now, now))
            for run_id in stale:
                self._event_locked(
                    run_id, now, "interrupted",
                    "Application stopped before the run completed.")

    def close(self):
        with self._lock:
            self._db.close()

    def start_run(self, device_id, device_name, simulated, profile, steps,
                  lab_id=None):
        run_id = uuid.uuid4().hex
        now = time.time()
        lab = (str(lab_id).strip() if lab_id else "") or None
        with self._lock, self._db:
            self._db.execute(
                """UPDATE runs SET status='superseded', updated_at=?
                   WHERE device_id=? AND status='interrupted'""",
                (now, device_id))
            self._db.execute(
                """INSERT INTO runs
                   (id, device_id, device_name, simulated, profile_name, lab_id,
                    profile_json, steps_json, started_at, updated_at, status,
                    step_index, step_total)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', 0, ?)""",
                (run_id, device_id, device_name, int(bool(simulated)),
                 profile.get("name"), lab,
                 json.dumps(profile, separators=(",", ":")),
                 json.dumps(steps, separators=(",", ":")), now, now, len(steps)))
            self._event_locked(run_id, now, "started",
                               f"Started profile {profile.get('name') or 'Unnamed'}")
        return run_id

    def resume_run(self, run_id, automatic=False):
        now = time.time()
        with self._lock, self._db:
            self._db.execute(
                """UPDATE runs SET status='running', updated_at=?, ended_at=NULL,
                       resumed_at=?, resume_automatic=?, resume_count=resume_count+1
                   WHERE id=?""", (now, now, int(bool(automatic)), run_id))
            message = ("Run resumed automatically" if automatic
                       else "Operator resumed the run")
            self._event_locked(run_id, now, "resumed", message)
        return now

    def update_progress(self, run_id, index, total, phase, remaining):
        if not run_id:
            return
        now = time.time()
        with self._lock, self._db:
            self._db.execute(
                """UPDATE runs SET updated_at=?, step_index=?, step_total=?,
                       phase=?, remaining_s=? WHERE id=?""",
                (now, int(index), int(total), phase, remaining, run_id))

    def add_event(self, run_id, kind, message=None, data=None, at=None):
        if not run_id:
            return
        with self._lock, self._db:
            self._event_locked(run_id, at or time.time(), kind, message, data)

    def _event_locked(self, run_id, at, kind, message=None, data=None):
        self._db.execute(
            """INSERT INTO run_events(run_id, t, kind, message, data_json)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, at, kind, message,
             json.dumps(data, separators=(",", ":")) if data is not None else None))

    def add_telemetry(self, run_id, sample):
        if not run_id:
            return
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO run_telemetry
                   (run_id, t, block_current, block_target, lid_current,
                    lid_target, lid_status) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, sample["t"], sample.get("block_current"),
                 sample.get("block_target"), sample.get("lid_current"),
                 sample.get("lid_target"), sample.get("lid_status")))

    def finish(self, run_id, status):
        if not run_id:
            return
        now = time.time()
        with self._lock, self._db:
            self._db.execute(
                """UPDATE runs SET status=?, updated_at=?, ended_at=?,
                       remaining_s=NULL WHERE id=?""", (status, now, now, run_id))
            self._event_locked(run_id, now, status, f"Run {status}")

    def interrupt(self, run_id, reason):
        if not run_id:
            return
        now = time.time()
        with self._lock, self._db:
            self._db.execute(
                """UPDATE runs SET status='interrupted', updated_at=?,
                       interrupted_at=?, resumed_at=NULL, resume_automatic=0,
                       interrupted_step_index=step_index,
                       interruption_reason=? WHERE id=?""",
                (now, now, reason, run_id))
            self._event_locked(run_id, now, "interrupted", reason)
        return now

    def latest_resumable(self, device_id):
        with self._lock:
            row = self._db.execute(
                """SELECT * FROM runs WHERE device_id=? AND status='interrupted'
                   ORDER BY updated_at DESC LIMIT 1""", (device_id,)).fetchone()
        return self._decode_run(row) if row else None

    def latest_run(self, device_id):
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM runs WHERE device_id=? ORDER BY started_at DESC LIMIT 1",
                (device_id,)).fetchone()
        return self._decode_run(row) if row else None

    def list_runs(self, limit=100):
        """Recent runs, newest first, as slim rows (no profile/steps payload)."""
        with self._lock:
            rows = self._db.execute(
                """SELECT id, device_id, device_name, simulated, profile_name,
                          lab_id, started_at, ended_at, status, step_index,
                          step_total, phase, resume_count, interruption_reason
                   FROM runs ORDER BY started_at DESC, id DESC LIMIT ?""",
                (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id, details=False):
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None
            result = self._decode_run(row)
            if details:
                result["events"] = [dict(r) for r in self._db.execute(
                    "SELECT t, kind, message, data_json FROM run_events "
                    "WHERE run_id=? ORDER BY t", (run_id,))]
                result["telemetry"] = [dict(r) for r in self._db.execute(
                    """SELECT t, block_current, block_target, lid_current,
                              lid_target, lid_status FROM run_telemetry
                       WHERE run_id=? ORDER BY t""", (run_id,))]
        return result

    @staticmethod
    def _decode_run(row):
        result = dict(row)
        result["simulated"] = bool(result["simulated"])
        result["resume_automatic"] = bool(result.get("resume_automatic", 0))
        result["profile"] = json.loads(result.pop("profile_json"))
        result["steps"] = json.loads(result.pop("steps_json"))
        return result


def _when(epoch):
    if epoch is None:
        return "—"
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def _temperature(value):
    return "—" if value is None else f"{value:.2f} °C"


# ============================================================================
#  PDF report — a tiny vector layout engine on raw PDF (base-14 fonts only),
#  so the console keeps its single dependency.
# ============================================================================

# Brand palette, mirrored from web/style.css.
_PLUM    = (0.169, 0.106, 0.180)   # #2B1B2E  header band
_INK     = (0.106, 0.063, 0.125)   # #1B1020  primary text
_MUTED   = (0.549, 0.486, 0.584)   # #8C7C95  labels / captions
_SOFT    = (0.651, 0.580, 0.686)   # soft violet, text on the plum band
_MAGENTA = (0.878, 0.204, 0.890)   # #E034E3  block series
_JADE    = (0.000, 0.659, 0.471)   # #00A878  lid series
_CRIT    = (0.910, 0.333, 0.427)   # #E8556D  interruptions
_RULE    = (0.855, 0.827, 0.867)   # hairlines
_ZEBRA   = (0.965, 0.953, 0.973)   # table banding
_WHITE   = (1.000, 1.000, 1.000)

_PAGE_W, _PAGE_H = 612, 792
_MARGIN = 48
_F_H, _F_B, _F_O, _F_M, _F_MB = "F1", "F2", "F3", "F4", "F5"

_STATUS_COLOR = {
    "completed": _JADE,
    "running": _MAGENTA,
    "stopped": _MUTED,
    "superseded": _MUTED,
    "interrupted": _CRIT,
}


def _duration(started, ended):
    if not started or not ended:
        return "—"
    total = max(0, int(ended - started))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def render_run_pdf(run):
    """Render the run report as a polished, dependency-free vector PDF."""
    pdf = _Pdf()
    _pdf_header(pdf, run)
    _pdf_meta(pdf, run)
    _pdf_steps(pdf, run)
    _pdf_trace(pdf, run)
    _pdf_events(pdf, run)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return pdf.finalize(
        f"BUILT DNA console  ·  run {run['id'][:8]}  ·  {stamp}")


def _pdf_header(pdf, run):
    pdf.rect(0, _PAGE_H - 76, _PAGE_W, 76, _PLUM)
    pdf.rect(0, _PAGE_H - 79, _PAGE_W, 3, _MAGENTA)
    pdf.text(_MARGIN, _PAGE_H - 27, "BUILT DNA THERMOCYCLER CONSOLE",
             _F_B, 7.5, _SOFT, tracking=2.2)
    pdf.text(_MARGIN, _PAGE_H - 58, "Run Report", _F_B, 22, _WHITE)
    color = _STATUS_COLOR.get(run["status"], _MUTED)
    label = run["status"].upper()
    x = _PAGE_W - _MARGIN - len(label) * 5.7
    pdf.rect(x - 15, _PAGE_H - 33, 7, 7, color)
    pdf.text(x, _PAGE_H - 27, label, _F_MB, 9.5, _WHITE)
    pdf._y = _PAGE_H - 119


def _pdf_meta(pdf, run):
    lab = run.get("lab_id")
    resume = int(run.get("resume_count") or 0)
    resume_txt = (str(resume) + (" · automatic"
                                 if resume and run.get("resume_automatic") else ""))
    entries = [
        ("LAB / LPD", lab or "—", _F_B, 11, _MAGENTA if lab else _MUTED),
        ("PROFILE", run.get("profile_name") or "Unnamed", _F_H, 10, _INK),
        ("STARTED", _when(run["started_at"]), _F_H, 9.5, _INK),
        ("INSTRUMENT", f"{run['device_name']} · {run['device_id']}",
         _F_H, 9.5, _INK),
        ("ENDED", _when(run.get("ended_at")), _F_H, 9.5, _INK),
        ("STATUS", run["status"].upper(), _F_B, 9.5,
         _STATUS_COLOR.get(run["status"], _MUTED)),
        ("DURATION", _duration(run.get("started_at"), run.get("ended_at")),
         _F_H, 9.5, _INK),
        ("RESUMES", resume_txt, _F_H, 9.5, _INK),
        ("LAST CHECKPOINT", _when(run.get("updated_at")), _F_H, 9.5, _INK),
        ("RUN ID", run["id"], _F_M, 8, _MUTED),
    ]
    if run.get("simulated"):
        entries.append(("INSTRUMENT TYPE", "simulated", _F_H, 9.5, _MUTED))
    for i in range(0, len(entries), 2):
        pdf.ensure(42)
        y = pdf._y
        for j, (label, value, font, size, color) in enumerate(entries[i:i + 2]):
            x = _MARGIN + j * 282
            pdf.text(x, y, label, _F_B, 7, _MUTED, tracking=1.1)
            pdf.text(x, y - 15, str(value), font, size, color)
        pdf._y = y - 42
    # Interruption / recovery callout.
    if run.get("interrupted_at"):
        interrupted_index = run.get("interrupted_step_index")
        step_index = min(
            run.get("step_index", 0) if interrupted_index is None
            else interrupted_index,
            max(0, len(run.get("steps", [])) - 1))
        step = run.get("steps", [{}])[step_index] if run.get("steps") else {}
        cycle = step.get("cycle")
        cycles = step.get("cycles")
        cycle_text = (f"cycle {cycle}/{cycles}" if cycle and cycles
                      else "cycle unknown")
        lines = [
            f"Power/communication loss detected {_when(run['interrupted_at'])} — "
            f"{cycle_text}, step {step_index + 1}/{run.get('step_total', 0)}"]
        if run.get("interruption_reason"):
            lines.append(f"Interruption: {run['interruption_reason']}")
        if run.get("resumed_at"):
            mode = "automatically" if run.get("resume_automatic") else "by operator"
            lines.append(f"Resumed {mode}: {_when(run['resumed_at'])}")
        wrapped = []
        for line in lines:
            wrapped.extend(textwrap.wrap(line, width=92) or [""])
        box_h = 18 + 12.5 * len(wrapped)
        pdf.ensure(box_h + 10)
        top = pdf._y
        pdf.rect(_MARGIN, top - box_h, _PAGE_W - 2 * _MARGIN, box_h,
                 (0.984, 0.937, 0.945))
        pdf.rect(_MARGIN, top - box_h, 3, box_h, _CRIT)
        y = top - 16
        for line in wrapped:
            pdf.text(_MARGIN + 12, y, line, _F_H, 8.5, (0.62, 0.16, 0.24))
            y -= 12.5
        pdf._y = top - box_h - 22
    else:
        pdf._y -= 8


def _pdf_section(pdf, title):
    pdf.ensure(48)
    y = pdf._y - 14
    pdf.text(_MARGIN, y, title, _F_B, 8, _MUTED, tracking=1.6)
    pdf.rule(_MARGIN, y - 7, _PAGE_W - _MARGIN, y - 7, _RULE, 0.8)
    pdf._y = y - 26


def _pdf_steps(pdf, run):
    steps = run.get("steps") or []
    _pdf_section(pdf, f"PROFILE STEPS ({len(steps)})")
    if not steps:
        pdf.text(_MARGIN, pdf._y, "No steps recorded.", _F_O, 9, _MUTED)
        pdf._y -= 26
        return
    col_n, col_label, col_temp = 48, 86, 452
    col_dur = _PAGE_W - _MARGIN
    pdf.ensure(30)
    y = pdf._y
    pdf.text(col_n, y, "N", _F_B, 7, _MUTED, tracking=1.1)
    pdf.text(col_label, y, "STAGE / STEP", _F_B, 7, _MUTED, tracking=1.1)
    pdf.text(col_temp - len("TEMP") * 4.2, y, "TEMP", _F_B, 7,
             _MUTED, tracking=1.1)
    pdf.text(col_dur - len("DURATION") * 4.2, y, "DURATION", _F_B, 7,
             _MUTED, tracking=1.1)
    pdf._y = y - 8
    for n, step in enumerate(steps, 1):
        pdf.ensure(15)
        y = pdf._y
        if n % 2 == 0:
            pdf.rect(_MARGIN, y - 11.5, _PAGE_W - 2 * _MARGIN, 13.5, _ZEBRA)
        temp = step.get("temp")
        temp_s = "—" if temp is None else f"{float(temp):.1f} °C"
        secs = step.get("seconds")
        dur = "indefinite" if not secs else f"{int(secs)} s"
        pdf.text(col_n, y - 2.5, str(n), _F_M, 8, _MUTED)
        pdf.text(col_label, y - 2.5, str(step.get("label") or "Step"),
                 _F_H, 9, _INK)
        pdf.text(col_temp - len(temp_s) * 5.1, y - 2.5, temp_s, _F_M, 8.5, _INK)
        pdf.text(col_dur - len(dur) * 5.1, y - 2.5, dur, _F_M, 8.5, _MUTED)
        pdf._y = y - 13.5
    pdf.rule(_MARGIN, pdf._y + 2, _PAGE_W - _MARGIN, pdf._y + 2, _RULE, 0.8)
    pdf._y -= 20


def _pdf_trace(pdf, run):
    telemetry = [s for s in (run.get("telemetry") or []) if s.get("t")]
    _pdf_section(pdf, "TEMPERATURE TRACE")
    if not telemetry:
        pdf.text(_MARGIN, pdf._y, "No telemetry recorded for this run.",
                 _F_O, 9, _MUTED)
        pdf._y -= 28
        return
    chart_h = 176
    x0, x1 = 64, _PAGE_W - _MARGIN
    pdf.ensure(chart_h + 44)
    top = pdf._y
    bottom = top - chart_h
    t0, t1 = telemetry[0]["t"], telemetry[-1]["t"]
    span = max(1.0, t1 - t0)
    for grid_temp in (0, 25, 50, 75, 100):
        gy = bottom + (grid_temp / 110.0) * chart_h
        pdf.rule(x0, gy, x1, gy, _RULE, 0.5)
        lab = str(grid_temp)
        pdf.text(x0 - 8 - len(lab) * 4.2, gy - 2.5, lab, _F_M, 7, _MUTED)
    pdf.text(x0 - 8 - len("°C") * 4.2, top + 6, "°C", _F_M, 7, _MUTED)
    ticks = 5
    for i in range(ticks):
        x = x0 + (x1 - x0) * i / (ticks - 1)
        t = t0 + span * i / (ticks - 1)
        lab = datetime.fromtimestamp(t).astimezone().strftime("%H:%M:%S")
        pdf.text(min(x, x1 - len(lab) * 4.2), bottom - 12, lab, _F_M, 7, _MUTED)
        pdf.rule(x, bottom, x, bottom + 3.5, _MUTED, 0.5)
    pdf.rule(x0, bottom, x1, bottom, _MUTED, 0.9)
    pdf.rule(x0, bottom, x0, top, _MUTED, 0.9)

    def series(key, color):
        pts = [s for s in telemetry if s.get(key) is not None]
        if not pts:
            return
        stride = max(1, len(pts) // 300)
        mapped = []
        for s in pts[::stride]:
            x = x0 + (x1 - x0) * (s["t"] - t0) / span
            c = min(110.0, max(0.0, s[key]))
            mapped.append((x, bottom + (c / 110.0) * chart_h))
        if len(mapped) >= 2:
            pdf.polyline(mapped, color, 1.2)
        else:
            pdf.rect(mapped[0][0] - 2, mapped[0][1] - 2, 4, 4, color)

    series("lid_current", _JADE)
    series("block_current", _MAGENTA)
    ly = top - 8
    pdf.rule(x1 - 128, ly, x1 - 108, ly, _JADE, 2.0)
    pdf.text(x1 - 104, ly - 2.5, "Lid", _F_H, 8, _MUTED)
    pdf.rule(x1 - 62, ly, x1 - 42, ly, _MAGENTA, 2.0)
    pdf.text(x1 - 38, ly - 2.5, "Block", _F_H, 8, _MUTED)
    pdf._y = bottom - 30
    summary = []
    for name, key in (("Block", "block_current"), ("Lid", "lid_current")):
        values = [s[key] for s in telemetry if s.get(key) is not None]
        if values:
            summary.append(f"{name}  min {_temperature(min(values))}"
                           f" · max {_temperature(max(values))}")
    pdf.text(_MARGIN, pdf._y - 2, "      ".join(summary), _F_H, 9, _MUTED)
    pdf._y -= 20


def _pdf_events(pdf, run):
    events = run.get("events") or []
    semantic = [e for e in events if e.get("kind") != "log"]
    _pdf_section(pdf, f"EVENT LOG ({len(semantic)})")
    if not semantic:
        pdf.text(_MARGIN, pdf._y, "No events recorded.", _F_O, 9, _MUTED)
        pdf._y -= 24
    for event in semantic:
        ts = datetime.fromtimestamp(event["t"]).astimezone(
        ).strftime("%m-%d %H:%M:%S")
        line = f"{ts}  [{event.get('kind')}] {event.get('message') or ''}"
        lines = textwrap.wrap(line, width=112,
                              subsequent_indent=" " * (len(ts) + 2)) or [""]
        color = _CRIT if event.get("kind") == "interrupted" else _INK
        for ln in lines:
            pdf.ensure(12)
            y = pdf._y
            pdf.text(_MARGIN, y, ln, _F_M, 7.5, color)
            pdf._y = y - 11
    if len(semantic) != len(events):
        pdf.ensure(12)
        pdf.text(_MARGIN, pdf._y - 4,
                 "Serial command log omitted from the report — see the "
                 "console Log tab.", _F_O, 7.5, _MUTED)
        pdf._y -= 20
    pdf._y -= 6


def _pdf_escape(text):
    clean = str(text).replace("\r", " ").replace("\n", " ")
    return (clean.encode("cp1252", errors="replace").decode("cp1252")
                .replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)"))


class _Pdf:
    """A tiny multi-page canvas emitting raw PDF content-stream operators.

    Coordinates are top-down: ``_y`` is the current baseline, and every
    emitter decrements it.  ``ensure()`` starts a fresh page when the
    remaining room would clip the next block.
    """

    def __init__(self):
        self._pages = [[]]
        self._y = _PAGE_H - _MARGIN

    def _new_page(self):
        self._pages.append([])
        self._y = _PAGE_H - _MARGIN

    def ensure(self, height):
        if self._y - height < _MARGIN:
            self._new_page()

    def _emit(self, op):
        self._pages[-1].append(op)

    def text(self, x, y, s, font=_F_H, size=10, color=_INK, tracking=None):
        track = f" {tracking} Tw" if tracking else ""
        self._emit(
            f"BT /{font} {size} Tf {color[0]:.3f} {color[1]:.3f} "
            f"{color[2]:.3f} rg{track} 1 0 0 1 {x:.1f} {y:.1f} Tm "
            f"({_pdf_escape(s)}) Tj ET")

    def rule(self, x1, y1, x2, y2, color=_RULE, width=0.7):
        self._emit(
            f"{width} w {color[0]:.3f} {color[1]:.3f} {color[2]:.3f} RG "
            f"{x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S")

    def rect(self, x, y, w, h, color):
        self._emit(
            f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg "
            f"{x:.1f} {y:.1f} {w:.1f} {h:.1f} re f")

    def polyline(self, points, color, width=1.2):
        ops = [f"{width} w {color[0]:.3f} {color[1]:.3f} {color[2]:.3f} RG",
               f"{points[0][0]:.1f} {points[0][1]:.1f} m"]
        ops.extend(f"{x:.1f} {y:.1f} l" for x, y in points[1:])
        ops.append("S")
        self._emit(" ".join(ops))

    def finalize(self, footer_left):
        total = len(self._pages)
        for n, ops in enumerate(self._pages, 1):
            ops.append(
                f"{_RULE[0]:.3f} {_RULE[1]:.3f} {_RULE[2]:.3f} RG 0.7 w "
                f"{_MARGIN} 42 m {_PAGE_W - _MARGIN} 42 l S")
            for x, label in ((_MARGIN, footer_left),
                             (None, f"page {n} of {total}")):
                if x is None:
                    x = _PAGE_W - _MARGIN - len(label) * 4.5
                ops.append(
                    f"BT /{_F_M} 7.5 Tf {_MUTED[0]:.3f} {_MUTED[1]:.3f} "
                    f"{_MUTED[2]:.3f} rg 1 0 0 1 {x:.1f} 30 Tm "
                    f"({_pdf_escape(label)}) Tj ET")
        return _pdf_assemble(self._pages)


def _pdf_assemble(page_ops):
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (b"<< /Type /Pages /Count " + str(len(page_ops)).encode() +
            b" /Kids [" + b" ".join(
                f"{8 + n * 2} 0 R".encode() for n in range(len(page_ops))) +
            b"] >>"),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique /Encoding /WinAnsiEncoding >>",
        6: b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
        7: b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold /Encoding /WinAnsiEncoding >>",
    }
    for n, ops in enumerate(page_ops):
        page_id, content_id = 8 + n * 2, 9 + n * 2
        stream = b"\n".join(op.encode("cp1252", "replace") for op in ops)
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R "
            f"/F4 6 0 R /F5 7 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode()
        objects[content_id] = (b"<< /Length " + str(len(stream)).encode() +
                               b" >>\nstream\n" + stream + b"\nendstream")

    highest = max(objects)
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0] * (highest + 1)
    for obj_id in range(1, highest + 1):
        offsets[obj_id] = len(output)
        output.extend(f"{obj_id} 0 obj\n".encode())
        output.extend(objects[obj_id])
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {highest + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {highest + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)

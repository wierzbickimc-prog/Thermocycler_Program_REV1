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
                    ("resume_automatic", "INTEGER NOT NULL DEFAULT 0")):
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

    def start_run(self, device_id, device_name, simulated, profile, steps):
        run_id = uuid.uuid4().hex
        now = time.time()
        with self._lock, self._db:
            self._db.execute(
                """UPDATE runs SET status='superseded', updated_at=?
                   WHERE device_id=? AND status='interrupted'""",
                (now, device_id))
            self._db.execute(
                """INSERT INTO runs
                   (id, device_id, device_name, simulated, profile_name,
                    profile_json, steps_json, started_at, updated_at, status,
                    step_index, step_total)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', 0, ?)""",
                (run_id, device_id, device_name, int(bool(simulated)),
                 profile.get("name"), json.dumps(profile, separators=(",", ":")),
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


def render_run_pdf(run):
    """Render a compact, valid PDF without requiring ReportLab."""
    lines = [
        "BUILT DNA Thermocycler Run Report",
        "",
        f"Run ID: {run['id']}",
        f"Instrument: {run['device_name']} ({run['device_id']})",
        f"Profile: {run.get('profile_name') or 'Unnamed'}",
        f"Status: {run['status']}",
        f"Started: {_when(run['started_at'])}",
        f"Last checkpoint: {_when(run['updated_at'])}",
        f"Ended: {_when(run.get('ended_at'))}",
        f"Resumes: {run.get('resume_count', 0)}",
        (f"Checkpoint progress: step {run.get('step_index', 0) + 1}/"
         f"{run.get('step_total', 0)}, phase {run.get('phase') or 'starting'}, "
         f"remaining {run.get('remaining_s') if run.get('remaining_s') is not None else '—'} s"),
    ]
    if run.get("interruption_reason"):
        lines.append(f"Interruption: {run['interruption_reason']}")
    if run.get("interrupted_at"):
        interrupted_index = run.get("interrupted_step_index")
        step_index = min(
            run.get("step_index", 0) if interrupted_index is None else interrupted_index,
            max(0, len(run.get("steps", [])) - 1))
        step = run.get("steps", [{}])[step_index] if run.get("steps") else {}
        cycle = step.get("cycle")
        cycles = step.get("cycles")
        cycle_text = f"cycle {cycle}/{cycles}" if cycle and cycles else "cycle unknown"
        lines.append(
            f"Power/communication loss detected: {_when(run['interrupted_at'])}, "
            f"{cycle_text}, step {step_index + 1}/{run.get('step_total', 0)}")
        if run.get("resumed_at"):
            mode = "automatically" if run.get("resume_automatic") else "by operator"
            lines.append(f"Resumed {mode}: {_when(run['resumed_at'])}")
    lines.extend(["", "Profile steps"])
    for n, step in enumerate(run.get("steps", []), 1):
        duration = "indefinite" if not step.get("seconds") else f"{step['seconds']} s"
        lines.append(f"{n}. {step.get('label', 'Step')} — {step.get('temp')} °C, {duration}")

    telemetry = run.get("telemetry", [])
    lines.extend(["", f"Telemetry ({len(telemetry)} samples)"])
    for label, key in (("Block", "block_current"), ("Lid", "lid_current")):
        values = [s[key] for s in telemetry if s.get(key) is not None]
        if values:
            lines.append(f"{label}: min {_temperature(min(values))}, "
                         f"max {_temperature(max(values))}")
    if telemetry:
        stride = max(1, (len(telemetry) + 199) // 200)
        for sample in telemetry[::stride]:
            lines.append(
                f"{_when(sample['t'])}  block {_temperature(sample['block_current'])} "
                f"(target {_temperature(sample['block_target'])}); lid "
                f"{_temperature(sample['lid_current'])} "
                f"(target {_temperature(sample['lid_target'])}); "
                f"lid {sample.get('lid_status') or 'unknown'}")

    lines.extend(["", "Event log"])
    for event in run.get("events", []):
        message = event.get("message") or ""
        lines.append(f"{_when(event['t'])}  [{event['kind']}] {message}")

    wrapped = []
    for line in lines:
        wrapped.extend(textwrap.wrap(str(line), width=96,
                                     subsequent_indent="    ") or [""])
    return _pdf_from_lines(wrapped)


def _pdf_escape(text):
    clean = str(text).replace("\r", " ").replace("\n", " ")
    raw = clean.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _pdf_from_lines(lines):
    per_page = 53
    pages = [lines[n:n + per_page] for n in range(0, len(lines), per_page)] or [[]]
    page_ids = [4 + n * 2 for n in range(len(pages))]
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (b"<< /Type /Pages /Count " + str(len(pages)).encode() + b" /Kids [" +
            b" ".join(f"{obj} 0 R".encode() for obj in page_ids) + b"] >>"),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    for n, page_lines in enumerate(pages):
        page_id = page_ids[n]
        content_id = page_id + 1
        commands = [b"BT", b"/F1 9 Tf", b"48 756 Td", b"12.5 TL"]
        for line in page_lines:
            commands.append(b"(" + _pdf_escape(line) + b") Tj T*")
        commands.append(b"ET")
        stream = b"\n".join(commands)
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
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

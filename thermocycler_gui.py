#!/usr/bin/env python3
"""
Opentrons Thermocycler Module GEN 1 - Desktop Control GUI
==========================================================

Direct USB-serial control of an Opentrons Thermocycler Module (GEN 1).
No OT-2 / Flex robot required - the module plugs straight into this computer
over USB and is driven with its native G-code serial protocol.

All hardware/protocol logic lives in ``thermocycler_core.py`` (which has no
GUI dependency and is unit-tested separately). This file is the Tkinter UI.

Features
--------
* Auto-detected serial-port picker, plus a built-in Simulator for demos.
* Manual block-temp / lid-temp control with hold time & sample volume, and
  live temperature readouts.
* Lid open / close / plate-lift, and deactivate block / lid / all.
* PCR profile builder (stages with repeat cycles), save/load JSON, and a
  background runner with software-timed holds, step progress and countdown.
* Real-time block + lid temperature graph.
* Scrolling command/response log.

Dependencies:  pyserial, matplotlib   (Tkinter ships with Python)
    pip install pyserial matplotlib
Run:
    python thermocycler_gui.py
"""

import json
import queue
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import thermocycler_core as core
from thermocycler_core import (
    BLOCK_MIN_C, BLOCK_MAX_C, LID_MIN_C, LID_MAX_C,
    DEFAULT_PROFILE, HAVE_SERIAL, Worker, flatten_profile,
)

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    HAVE_MPL = True
except Exception:  # pragma: no cover
    HAVE_MPL = False


# ============================================================================
#  Main application window
# ============================================================================
class App(tk.Tk):
    GRAPH_WINDOW = 900  # seconds shown on the rolling graph

    def __init__(self):
        super().__init__()
        self.title("Opentrons Thermocycler GEN 1 - Control")
        self.geometry("1100x720")
        self.minsize(940, 620)

        self.out_q = queue.Queue()
        self.worker = Worker(self.out_q)
        self.worker.start()

        self.connected = False
        self.running = False
        self._run_total = 0
        self.stages = json.loads(json.dumps(DEFAULT_PROFILE["stages"]))

        self._t0 = time.time()
        self._hist = {"t": [], "block": [], "lid": []}

        self._build_style()
        self._build_layout()
        self._refresh_ports()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._drain_queue)

    # ---- styling ---------------------------------------------------------
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Readout.TLabel", font=("TkDefaultFont", 20, "bold"))
        style.configure("Sub.TLabel", foreground="#666")
        style.configure("Danger.TButton", foreground="#a00")

    # ---- layout ----------------------------------------------------------
    def _build_layout(self):
        self._build_connection_bar()
        body = ttk.Frame(self, padding=(8, 4))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self._build_left_panel(body)
        self._build_right_panel(body)

    def _build_connection_bar(self):
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="Port:").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(bar, textvariable=self.port_var,
                                       width=34, state="readonly")
        self.port_combo.pack(side="left", padx=4)
        ttk.Button(bar, text="Refresh", command=self._refresh_ports).pack(side="left")
        self.connect_btn = ttk.Button(bar, text="Connect", command=self._toggle_connect)
        self.connect_btn.pack(side="left", padx=8)
        self.status_lbl = ttk.Label(bar, text="Disconnected", style="Sub.TLabel")
        self.status_lbl.pack(side="left", padx=8)

    def _build_left_panel(self, parent):
        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 8))

        ro = ttk.LabelFrame(left, text="Live status", padding=8)
        ro.pack(fill="x")
        self.block_ro = ttk.Label(ro, text="-- °C", style="Readout.TLabel")
        self.block_ro.grid(row=0, column=0, sticky="w")
        ttk.Label(ro, text="Block", style="Sub.TLabel").grid(row=1, column=0, sticky="w")
        self.block_tgt = ttk.Label(ro, text="target --", style="Sub.TLabel")
        self.block_tgt.grid(row=2, column=0, sticky="w", pady=(0, 6))

        self.lid_ro = ttk.Label(ro, text="-- °C", style="Readout.TLabel")
        self.lid_ro.grid(row=0, column=1, sticky="w", padx=(24, 0))
        ttk.Label(ro, text="Lid", style="Sub.TLabel").grid(row=1, column=1, sticky="w", padx=(24, 0))
        self.lid_tgt = ttk.Label(ro, text="target --", style="Sub.TLabel")
        self.lid_tgt.grid(row=2, column=1, sticky="w", padx=(24, 0))
        self.lid_status_lbl = ttk.Label(ro, text="Lid: unknown", style="Sub.TLabel")
        self.lid_status_lbl.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        bc = ttk.LabelFrame(left, text="Block temperature", padding=8)
        bc.pack(fill="x", pady=(8, 0))
        self.block_temp_var = tk.StringVar(value="95")
        self.block_hold_var = tk.StringVar(value="")
        self.volume_var = tk.StringVar(value="25")
        self._labeled_entry(bc, "Target (°C)", self.block_temp_var, 0)
        self._labeled_entry(bc, "Hold (s, optional)", self.block_hold_var, 1)
        self._labeled_entry(bc, "Volume (µL, optional)", self.volume_var, 2)
        ttk.Button(bc, text="Set block", command=self._set_block).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        lc = ttk.LabelFrame(left, text="Lid temperature", padding=8)
        lc.pack(fill="x", pady=(8, 0))
        self.lid_temp_var = tk.StringVar(value="105")
        self._labeled_entry(lc, "Target (°C)", self.lid_temp_var, 0)
        ttk.Button(lc, text="Set lid", command=self._set_lid).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        lm = ttk.LabelFrame(left, text="Lid movement", padding=8)
        lm.pack(fill="x", pady=(8, 0))
        ttk.Button(lm, text="Open", command=lambda: self._simple("open_lid")).grid(
            row=0, column=0, sticky="ew", padx=2)
        ttk.Button(lm, text="Close", command=lambda: self._simple("close_lid")).grid(
            row=0, column=1, sticky="ew", padx=2)
        ttk.Button(lm, text="Plate lift", command=lambda: self._simple("plate_lift")).grid(
            row=0, column=2, sticky="ew", padx=2)
        for c in range(3):
            lm.columnconfigure(c, weight=1)

        dz = ttk.LabelFrame(left, text="Deactivate", padding=8)
        dz.pack(fill="x", pady=(8, 0))
        ttk.Button(dz, text="Block", command=lambda: self._simple("deactivate_block")).grid(
            row=0, column=0, sticky="ew", padx=2)
        ttk.Button(dz, text="Lid", command=lambda: self._simple("deactivate_lid")).grid(
            row=0, column=1, sticky="ew", padx=2)
        ttk.Button(dz, text="ALL", style="Danger.TButton",
                   command=lambda: self._simple("deactivate_all")).grid(
            row=0, column=2, sticky="ew", padx=2)
        for c in range(3):
            dz.columnconfigure(c, weight=1)

    def _labeled_entry(self, parent, label, var, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        e = ttk.Entry(parent, textvariable=var, width=10)
        e.grid(row=row, column=1, sticky="e", pady=2, padx=(6, 0))
        parent.columnconfigure(0, weight=1)
        return e

    def _build_right_panel(self, parent):
        nb = ttk.Notebook(parent)
        nb.grid(row=0, column=1, sticky="nsew")
        self.nb = nb
        self._build_profile_tab(nb)
        self._build_graph_tab(nb)
        self._build_log_tab(nb)

    # ---- profile tab -----------------------------------------------------
    def _build_profile_tab(self, nb):
        tab = ttk.Frame(nb, padding=8)
        nb.add(tab, text="PCR Profile")
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)

        opts = ttk.Frame(tab)
        opts.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.prof_lid_var = tk.StringVar(value=str(DEFAULT_PROFILE["lid_temp"]))
        self.prof_vol_var = tk.StringVar(value=str(DEFAULT_PROFILE["volume"]))
        self.preheat_var = tk.BooleanVar(value=DEFAULT_PROFILE["preheat_lid"])
        ttk.Label(opts, text="Lid temp (°C):").pack(side="left")
        ttk.Entry(opts, textvariable=self.prof_lid_var, width=7).pack(side="left", padx=(2, 12))
        ttk.Label(opts, text="Volume (µL):").pack(side="left")
        ttk.Entry(opts, textvariable=self.prof_vol_var, width=7).pack(side="left", padx=(2, 12))
        ttk.Checkbutton(opts, text="Preheat lid", variable=self.preheat_var).pack(side="left")

        tree_frame = ttk.Frame(tab)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=("temp", "time"),
                                 show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Stage / Step")
        self.tree.heading("temp", text="Temp (°C)")
        self.tree.heading("time", text="Time (s)")
        self.tree.column("#0", width=280)
        self.tree.column("temp", width=90, anchor="center")
        self.tree.column("time", width=90, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("active", background="#fff3b0")

        eb = ttk.Frame(tab)
        eb.grid(row=2, column=0, sticky="ew", pady=6)
        ttk.Button(eb, text="Add stage", command=self._add_stage).pack(side="left", padx=2)
        ttk.Button(eb, text="Add step", command=self._add_step).pack(side="left", padx=2)
        ttk.Button(eb, text="Edit", command=self._edit_item).pack(side="left", padx=2)
        ttk.Button(eb, text="Delete", command=self._delete_item).pack(side="left", padx=2)
        ttk.Button(eb, text="Up", command=lambda: self._move_item(-1)).pack(side="left", padx=2)
        ttk.Button(eb, text="Down", command=lambda: self._move_item(1)).pack(side="left", padx=2)
        ttk.Button(eb, text="Load", command=self._load_profile).pack(side="right", padx=2)
        ttk.Button(eb, text="Save", command=self._save_profile).pack(side="right", padx=2)

        rc = ttk.Frame(tab)
        rc.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self.run_btn = ttk.Button(rc, text="▶ Run profile", command=self._run_profile,
                                  state="disabled")
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(rc, text="■ Stop", command=self._stop_profile,
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.run_status = ttk.Label(rc, text="Idle", style="Sub.TLabel")
        self.run_status.pack(side="left", padx=12)
        self.run_progress = ttk.Progressbar(rc, mode="determinate", length=180)
        self.run_progress.pack(side="right")

        self._reload_tree()

    def _build_graph_tab(self, nb):
        tab = ttk.Frame(nb, padding=8)
        nb.add(tab, text="Graph")
        if not HAVE_MPL:
            ttk.Label(tab, text="matplotlib is not installed.\nRun:  pip install matplotlib",
                      justify="center").pack(expand=True)
            self.ax = None
            return
        self.fig = Figure(figsize=(6, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Temperature (°C)")
        self.ax.grid(True, alpha=0.3)
        (self.block_line,) = self.ax.plot([], [], label="Block", color="#c0392b")
        (self.lid_line,) = self.ax.plot([], [], label="Lid", color="#2980b9")
        self.ax.legend(loc="upper right")
        self.canvas = FigureCanvasTkAgg(self.fig, master=tab)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def _build_log_tab(self, nb):
        tab = ttk.Frame(nb, padding=4)
        nb.add(tab, text="Log")
        self.log = tk.Text(tab, height=10, wrap="none", state="disabled",
                           font=("TkFixedFont", 9))
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(tab, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set)
        self.log.tag_configure("tx", foreground="#1a6")
        self.log.tag_configure("rx", foreground="#555")
        self.log.tag_configure("err", foreground="#c00")
        self.log.tag_configure("info", foreground="#06c")

    # ================= profile tree management =================
    def _reload_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._item_map = {}
        for i, stage in enumerate(self.stages):
            label = stage["name"]
            cyc = int(stage.get("cycles", 1))
            if cyc > 1:
                label += f"   ×{cyc} cycles"
            sid = self.tree.insert("", "end", text=label, values=("", ""), open=True)
            self._item_map[sid] = ("stage", i)
            for j, st in enumerate(stage["steps"]):
                secs = st.get("seconds")
                secs_txt = "hold" if secs in (None, "", 0, "0") else str(secs)
                cid = self.tree.insert(sid, "end", text=f"  step {j + 1}",
                                       values=(st["temp"], secs_txt))
                self._item_map[cid] = ("step", i, j)

    def _selected(self):
        sel = self.tree.selection()
        return self._item_map.get(sel[0]) if sel else None

    def _add_stage(self):
        dlg = StageDialog(self, "New stage")
        if dlg.result:
            self.stages.append({"name": dlg.result["name"], "cycles": dlg.result["cycles"],
                                "steps": [{"temp": 95.0, "seconds": 30}]})
            self._reload_tree()

    def _add_step(self):
        info = self._selected()
        if not info:
            messagebox.showinfo("Add step", "Select a stage (or a step within it) first.")
            return
        dlg = StepDialog(self, "New step")
        if dlg.result:
            self.stages[info[1]]["steps"].append(dlg.result)
            self._reload_tree()

    def _edit_item(self):
        info = self._selected()
        if not info:
            return
        if info[0] == "stage":
            stage = self.stages[info[1]]
            dlg = StageDialog(self, "Edit stage", stage["name"], stage.get("cycles", 1))
            if dlg.result:
                stage["name"] = dlg.result["name"]
                stage["cycles"] = dlg.result["cycles"]
        else:
            _, i, j = info
            st = self.stages[i]["steps"][j]
            secs = st.get("seconds")
            dlg = StepDialog(self, "Edit step", st["temp"],
                             "" if secs in (None, 0, "0") else secs)
            if dlg.result:
                self.stages[i]["steps"][j] = dlg.result
        self._reload_tree()

    def _delete_item(self):
        info = self._selected()
        if not info:
            return
        if info[0] == "stage":
            del self.stages[info[1]]
        else:
            _, i, j = info
            del self.stages[i]["steps"][j]
            if not self.stages[i]["steps"]:
                del self.stages[i]
        self._reload_tree()

    def _move_item(self, delta):
        info = self._selected()
        if not info:
            return
        if info[0] == "stage":
            i, j = info[1], info[1] + delta
            if 0 <= j < len(self.stages):
                self.stages[i], self.stages[j] = self.stages[j], self.stages[i]
        else:
            _, i, k = info
            steps = self.stages[i]["steps"]
            m = k + delta
            if 0 <= m < len(steps):
                steps[k], steps[m] = steps[m], steps[k]
        self._reload_tree()

    def _save_profile(self):
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        data = {"lid_temp": self._to_float(self.prof_lid_var.get()),
                "volume": self._to_float(self.prof_vol_var.get()),
                "preheat_lid": self.preheat_var.get(), "stages": self.stages}
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        self._log_line(f"Saved profile to {path}", "info")

    def _load_profile(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self.stages = data["stages"]
            if data.get("lid_temp") is not None:
                self.prof_lid_var.set(str(data["lid_temp"]))
            if data.get("volume") is not None:
                self.prof_vol_var.set(str(data["volume"]))
            self.preheat_var.set(bool(data.get("preheat_lid", True)))
            self._reload_tree()
            self._log_line(f"Loaded profile from {path}", "info")
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))

    # ================= actions =================
    def _to_float(self, s):
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    def _validate_temp(self, val, lo, hi, name):
        if val is None:
            messagebox.showerror("Invalid input", f"Enter a numeric {name}.")
            return False
        if not (lo <= val <= hi):
            messagebox.showerror("Out of range", f"{name} must be between {lo} and {hi} °C.")
            return False
        return True

    def _set_block(self):
        temp = self._to_float(self.block_temp_var.get())
        if not self._validate_temp(temp, BLOCK_MIN_C, BLOCK_MAX_C, "block temperature"):
            return
        self.worker.submit("set_block", temp=temp,
                           hold=self._to_float(self.block_hold_var.get()),
                           volume=self._to_float(self.volume_var.get()))

    def _set_lid(self):
        temp = self._to_float(self.lid_temp_var.get())
        if not self._validate_temp(temp, LID_MIN_C, LID_MAX_C, "lid temperature"):
            return
        self.worker.submit("set_lid", temp=temp)

    def _simple(self, action):
        if not self.connected:
            return
        self.worker.submit(action)

    def _run_profile(self):
        steps = flatten_profile(self.stages)
        if not steps:
            messagebox.showinfo("Run", "Add at least one stage with a step.")
            return
        for s in steps:
            if not (BLOCK_MIN_C <= s["temp"] <= BLOCK_MAX_C):
                messagebox.showerror("Out of range",
                                     f"Step '{s['label']}' temp {s['temp']} °C is "
                                     f"outside {BLOCK_MIN_C}-{BLOCK_MAX_C}.")
                return
        self._run_total = len(steps)
        self.worker.submit("run_profile", steps=steps,
                           volume=self._to_float(self.prof_vol_var.get()),
                           lid_temp=self._to_float(self.prof_lid_var.get()),
                           preheat_lid=self.preheat_var.get())

    def _stop_profile(self):
        self.worker.submit("stop_run")

    # ================= connection =================
    def _refresh_ports(self):
        ports = [f"{dev}  -  {desc}" for dev, desc in core.available_ports()]
        ports.append("Simulator (demo - no hardware)")
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_combo.current(len(ports) - 1)

    def _toggle_connect(self):
        if self.connected:
            self.worker.submit("disconnect")
        else:
            sel = self.port_var.get()
            if not sel:
                messagebox.showinfo("Connect", "Choose a port first.")
                return
            simulate = sel.startswith("Simulator")
            port = None if simulate else sel.split("  -  ")[0]
            self.status_lbl.config(text="Connecting...")
            self.worker.submit("connect", port=port, simulate=simulate)

    # ================= queue draining / UI updates =================
    def _drain_queue(self):
        try:
            while True:
                self._handle_msg(self.out_q.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _handle_msg(self, msg):
        kind = msg["kind"]
        if kind == "log":
            self._log_line(msg["text"], msg.get("direction", "info"))
        elif kind == "connected":
            self.connected = True
            self.connect_btn.config(text="Disconnect")
            self.status_lbl.config(text=f"Connected: {msg['port']}")
            self.run_btn.config(state="normal")
        elif kind == "disconnected":
            self.connected = False
            self.connect_btn.config(text="Connect")
            self.status_lbl.config(text="Disconnected")
            self.run_btn.config(state="disabled")
            self._set_run_ui(False)
        elif kind in ("connect_failed", "error"):
            self.status_lbl.config(text="Disconnected")
            if kind == "connect_failed":
                messagebox.showerror("Connection failed", msg["text"])
        elif kind == "telemetry":
            self._update_telemetry(msg)
        elif kind == "run_started":
            self._set_run_ui(True)
            self.run_progress.config(maximum=msg["total"], value=0)
        elif kind == "run_step":
            self._update_run_step(msg)
        elif kind == "run_finished":
            self._set_run_ui(False)
            self.run_status.config(text="Stopped" if msg.get("aborted") else "Complete")
            self._clear_active_row()

    def _update_telemetry(self, msg):
        b_cur, b_tgt = msg["block_current"], msg["block_target"]
        l_cur, l_tgt = msg["lid_current"], msg["lid_target"]
        self.block_ro.config(text=f"{b_cur:.1f} °C" if b_cur is not None else "-- °C")
        self.lid_ro.config(text=f"{l_cur:.1f} °C" if l_cur is not None else "-- °C")
        self.block_tgt.config(text=f"target {b_tgt:.1f}" if b_tgt is not None else "target --")
        self.lid_tgt.config(text=f"target {l_tgt:.1f}" if l_tgt is not None else "target --")
        self.lid_status_lbl.config(text=f"Lid: {msg['lid_status']}")
        t = msg["t"] - self._t0
        self._hist["t"].append(t)
        self._hist["block"].append(b_cur if b_cur is not None else float("nan"))
        self._hist["lid"].append(l_cur if l_cur is not None else float("nan"))
        self._trim_history()
        self._redraw_graph()

    def _trim_history(self):
        if not self._hist["t"]:
            return
        cutoff = self._hist["t"][-1] - self.GRAPH_WINDOW
        while self._hist["t"] and self._hist["t"][0] < cutoff:
            for k in self._hist:
                self._hist[k].pop(0)

    def _redraw_graph(self):
        if not HAVE_MPL or self.ax is None:
            return
        self.block_line.set_data(self._hist["t"], self._hist["block"])
        self.lid_line.set_data(self._hist["t"], self._hist["lid"])
        if self._hist["t"]:
            self.ax.set_xlim(max(0, self._hist["t"][0]), self._hist["t"][-1] + 1)
            vals = [v for v in self._hist["block"] + self._hist["lid"] if v == v]
            if vals:
                self.ax.set_ylim(min(vals) - 5, max(vals) + 5)
        self.canvas.draw_idle()

    def _set_run_ui(self, running):
        self.running = running
        self.run_btn.config(state="disabled" if running
                            else ("normal" if self.connected else "disabled"))
        self.stop_btn.config(state="normal" if running else "disabled")
        if not running:
            self.run_progress.config(value=0)

    def _update_run_step(self, msg):
        idx = msg["index"]
        self.run_progress.config(value=idx)
        phase = msg["phase"]
        if phase == "ramp":
            txt = f"Step {idx + 1}/{self._run_total}: {msg['label']} - ramping to {msg['temp']:.0f}°C"
        elif phase == "hold_inf":
            txt = f"Step {idx + 1}/{self._run_total}: {msg['label']} - holding at {msg['temp']:.0f}°C (until stopped)"
        else:
            rem = msg.get("remaining")
            rem_txt = f" - {rem}s left" if rem is not None else ""
            txt = f"Step {idx + 1}/{self._run_total}: {msg['label']} - {msg['temp']:.0f}°C{rem_txt}"
        self.run_status.config(text=txt)
        self._highlight_active(idx)

    def _highlight_active(self, flat_idx):
        self._clear_active_row()
        counter, target = 0, None
        for i, stage in enumerate(self.stages):
            for _c in range(max(1, int(stage.get("cycles", 1)))):
                for j in range(len(stage["steps"])):
                    if counter == flat_idx:
                        target = (i, j)
                    counter += 1
        if target:
            for iid, info in self._item_map.items():
                if info[0] == "step" and info[1:] == target:
                    self.tree.item(iid, tags=("active",))
                    self.tree.see(iid)
                    break

    def _clear_active_row(self):
        for iid, info in getattr(self, "_item_map", {}).items():
            if info[0] == "step":
                self.tree.item(iid, tags=())

    def _log_line(self, text, direction="info"):
        prefix = {"tx": ">> ", "rx": "<< ", "err": "!! ", "info": "-- "}.get(direction, "   ")
        self.log.config(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S ") + prefix + text + "\n", direction)
        self.log.see("end")
        self.log.config(state="disabled")

    def _on_close(self):
        try:
            self.worker.submit("disconnect")
            self.worker.shutdown()
        finally:
            self.destroy()


# ============================================================================
#  Small modal dialogs for editing stages and steps
# ============================================================================
class StageDialog:
    def __init__(self, parent, title, name="New stage", cycles=1):
        self.result = None
        top = self.top = tk.Toplevel(parent)
        top.title(title)
        top.transient(parent)
        top.grab_set()
        ttk.Label(top, text="Stage name:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.name_var = tk.StringVar(value=name)
        ttk.Entry(top, textvariable=self.name_var, width=28).grid(row=0, column=1, padx=8)
        ttk.Label(top, text="Cycles (repeats):").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.cyc_var = tk.StringVar(value=str(cycles))
        ttk.Entry(top, textvariable=self.cyc_var, width=28).grid(row=1, column=1, padx=8)
        btns = ttk.Frame(top)
        btns.grid(row=2, column=0, columnspan=2, pady=8)
        ttk.Button(btns, text="OK", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=top.destroy).pack(side="left", padx=4)
        top.bind("<Return>", lambda e: self._ok())
        parent.wait_window(top)

    def _ok(self):
        try:
            cycles = max(1, int(float(self.cyc_var.get())))
        except ValueError:
            messagebox.showerror("Invalid", "Cycles must be a whole number.", parent=self.top)
            return
        self.result = {"name": self.name_var.get().strip() or "Stage", "cycles": cycles}
        self.top.destroy()


class StepDialog:
    def __init__(self, parent, title, temp=95.0, seconds=""):
        self.result = None
        top = self.top = tk.Toplevel(parent)
        top.title(title)
        top.transient(parent)
        top.grab_set()
        ttk.Label(top, text="Temperature (°C):").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.temp_var = tk.StringVar(value=str(temp))
        ttk.Entry(top, textvariable=self.temp_var, width=20).grid(row=0, column=1, padx=8)
        ttk.Label(top, text="Hold time (s):").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.time_var = tk.StringVar(value=str(seconds))
        ttk.Entry(top, textvariable=self.time_var, width=20).grid(row=1, column=1, padx=8)
        ttk.Label(top, text="(blank or 0 = hold indefinitely)",
                  foreground="#888").grid(row=2, column=0, columnspan=2, padx=8)
        btns = ttk.Frame(top)
        btns.grid(row=3, column=0, columnspan=2, pady=8)
        ttk.Button(btns, text="OK", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=top.destroy).pack(side="left", padx=4)
        top.bind("<Return>", lambda e: self._ok())
        parent.wait_window(top)

    def _ok(self):
        try:
            temp = float(self.temp_var.get())
        except ValueError:
            messagebox.showerror("Invalid", "Temperature must be numeric.", parent=self.top)
            return
        if not (BLOCK_MIN_C <= temp <= BLOCK_MAX_C):
            messagebox.showerror("Out of range",
                                 f"Temp must be {BLOCK_MIN_C}-{BLOCK_MAX_C} °C.", parent=self.top)
            return
        raw = self.time_var.get().strip()
        seconds = None
        if raw not in ("", "0"):
            try:
                seconds = int(float(raw))
            except ValueError:
                messagebox.showerror("Invalid", "Hold time must be numeric.", parent=self.top)
                return
        self.result = {"temp": temp, "seconds": seconds}
        self.top.destroy()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()

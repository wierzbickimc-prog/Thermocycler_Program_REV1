# Opentrons Thermocycler Module GEN 1 — Control Software

Control **Opentrons Thermocycler Modules (GEN 1)** directly over USB serial —
**no OT-2 / Flex robot required**. The modules plug straight into your computer
and are driven with their native G-code serial protocol.

Two front-ends share one hardware layer (`thermocycler_core.py`):

| | | |
|---|---|---|
| **BUILT DNA console** | `python serve.py` | Multi-instrument web UI in BUILT branding — **recommended** |
| Legacy desktop app | `python thermocycler_gui.py` | Single-instrument Tkinter window |

## BUILT DNA console

```bash
python serve.py                 # opens http://127.0.0.1:8765
python serve.py --port 9000 --no-browser
```

Stdlib only — no Flask/FastAPI. Live state reaches the browser over Server-Sent
Events; the page is a small ES-module SPA under `web/`.

## Interface gallery

<table>
  <tr>
    <td width="50%"><strong>Instrument fleet</strong><br><a href="https://github.com/wierzbickimc-prog/Thermocycler_Program_REV1/blob/main/docs/screenshots/fleet-overview.png"><img src="https://raw.githubusercontent.com/wierzbickimc-prog/Thermocycler_Program_REV1/main/docs/screenshots/fleet-overview.png" alt="Fleet overview showing three simulated thermocyclers"></a></td>
    <td width="50%"><strong>Device controls and profile editor</strong><br><a href="https://github.com/wierzbickimc-prog/Thermocycler_Program_REV1/blob/main/docs/screenshots/device-profile.png"><img src="https://raw.githubusercontent.com/wierzbickimc-prog/Thermocycler_Program_REV1/main/docs/screenshots/device-profile.png" alt="Thermocycler device controls and PCR profile editor"></a></td>
  </tr>
  <tr>
    <td width="50%"><strong>Profile administration</strong><br><a href="https://github.com/wierzbickimc-prog/Thermocycler_Program_REV1/blob/main/docs/screenshots/profile-administration.png"><img src="https://raw.githubusercontent.com/wierzbickimc-prog/Thermocycler_Program_REV1/main/docs/screenshots/profile-administration.png" alt="Administration screen for standard PCR profiles"></a></td>
    <td width="50%"><strong>Thermal QC</strong><br><a href="https://github.com/wierzbickimc-prog/Thermocycler_Program_REV1/blob/main/docs/screenshots/thermal-qc.png"><img src="https://raw.githubusercontent.com/wierzbickimc-prog/Thermocycler_Program_REV1/main/docs/screenshots/thermal-qc.png" alt="Thermal quality control setup interface"></a></td>
  </tr>
</table>

- **Instrument grid** — every connected thermocycler on one screen with live
  block/lid temperatures, run progress and lid state. Click a card to open it.
- **Animated instrument** — an isometric SVG of the module that reflects real
  state: the lid swings open and shut with `M126`/`M127`, the 96 wells tint with
  block temperature, and the chassis glows as it heats.
- **Named instruments** — click the name on the control screen to rename. Units
  are keyed by USB serial number where the OS exposes one, so a nickname survives
  reconnects and re-enumeration. Stored in `~/.builtdna/devices.json`.
- **Profile library** — five built-in presets (standard 3-step, 2-step fast,
  touchdown, colony PCR, restriction digest) plus your own saved profiles in
  `~/.builtdna/profiles/`.
- **Profile editor** — click any value in the stage table to edit it; add,
  duplicate, delete and reorder stages and steps; Save, or Save as… to fork a
  read-only preset. Profiles define whether the lid must be open or closed;
  starting a mismatched profile offers to move the lid and waits for the module
  to confirm its position. Built-in presets cannot be overwritten, but they can
  be deactivated in the profile manager to hide them from instrument dropdowns.
- **Busy-machine guard** — starting a run on an instrument that is already
  running or holding a target asks for confirmation first and lists exactly what
  it is doing before overriding.
- **Deep links** — `#/device/<id>/graph`, `/qc` and `/log` address a specific tab.

## Thermal QC

A QC tab that checks block accuracy *and* uniformity using a melting-point
standard, read visually by the operator — see `qc.py`.

The instrument has one block thermistor, so it cannot report well-to-well
spread. Loading a known-melting-point solid across the plate and recording the
setpoint at which each well turns clear gives you that spread directly.

- Pick a material (lauric / myristic / palmitic / stearic acid, or vanillin),
  and the sweep window defaults to ±4 °C around its melting point.
- The sweep is a step-and-dwell profile — it runs through the same tested
  runner as any other profile.
- Click each well on the 96-well map as it turns clear; the current setpoint is
  stamped against it.
- Results: mean melt vs nominal (**accuracy**) and the spread across the plate
  (**uniformity**), written to `~/.builtdna/qc/` as CSV and JSON.

Run it with the **lid open** — the observation is visual, so the heated lid stays
off for the whole sweep. Results read slightly high, because you are watching the
top of the sample while the block heats it from below; treat this as a
"within X °C of nominal" check rather than an absolute calibration.

**Never use gallium as the standard.** It is an ITS-90 fixed point and otherwise
ideal, but it attacks aluminium and would permanently damage the block.

Set `BUILTDNA_SIMULATORS=3` to populate the grid with simulated instruments for a
demo or for UI work with no hardware attached.

### Chart colours

The block/lid series (`#E034E3` magenta, `#00A878` jade) were chosen by running a
colour-vision-deficiency validator against the `#1B1020` surface rather than by
eye: deutan ΔE 15.8, normal-vision ΔE 40.4, contrast ≥ 3:1. Both series are also
direct-labelled at the line end, so identity never depends on colour alone.

## What the desktop app does

- **Connect** to the module's USB serial port (auto-detected), or run the built-in
  **Simulator** to try the whole interface with no hardware attached.
- **Block temperature** — set target with optional hold time and sample volume (µL),
  with a large live readout.
- **Lid temperature** — set target, live readout.
- **Lid movement** — open, close, plate lift.
- **Deactivate** — block, lid, or everything.
- **PCR profile builder** — stages with repeat cycles (e.g. denature/anneal/extend ×35),
  editable in a tree, saved/loaded as JSON. The runner uses **software-timed holds**:
  it sets each target, waits until the block reaches temperature, then holds for the
  configured time, showing a live per-step countdown and progress bar. A step time of
  `0` (or blank) means "hold indefinitely" — handy for a final 4 °C hold.
  With **Preheat lid** ticked the run waits for the lid to reach temperature
  before the first block step, to avoid condensation.
- **Stop** — ends the run *and* deactivates the block and lid. Closing the window
  while connected offers to do the same, since the module otherwise keeps its last
  commanded temperature after the GUI is gone.
- **Live graph** — block and lid temperature plotted over time.
- **Log** — every command sent and response received.

## Install

Requires Python 3.8+ **linked against Tcl/Tk 8.6 or newer**. Check before anything else:

```bash
python3 -c "import tkinter; print(tkinter.Tcl().eval('info patchlevel'))"
```

If that prints **8.5.x**, the GUI will open as a blank, unresponsive window that
pins a CPU core — it is not a bug in this program. macOS's built-in
`/usr/bin/python3` ships Tk 8.5.9 and is affected; use a Homebrew or python.org
build instead:

```bash
brew install python@3.11 python-tk@3.11
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt   # pyserial + matplotlib
```

On some Linux distros Tkinter is a separate package: `sudo apt install python3-tk`.

## Run

```bash
.venv/bin/python thermocycler_gui.py
```

Pick your port (or **Simulator (demo)**) from the dropdown, click **Connect**, and go.
The **Simulator** models heating/cooling and lid movement so you can explore the UI,
build profiles, and watch the graph without the instrument.

## Files

| File | Purpose |
|------|---------|
| `thermocycler_gui.py`   | The Tkinter GUI (front-end only). |
| `thermocycler_core.py`  | Serial protocol, simulator, and the background control worker. No GUI dependency — importable/reusable/testable on its own. |
| `test_protocol.py`      | Unit + integration tests for the protocol and profile logic. Run: `python test_protocol.py`. |
| `requirements.txt`      | `pyserial`, `matplotlib`. |

## Serial protocol (GEN 1)

Verified against the Opentrons driver source. 115200 baud, `\r\n` line terminator,
commands acknowledged by two `ok` lines.

| G-code | Action | Notes |
|--------|--------|-------|
| `M104 S<t> [H<s>] [V<µL>]` | Set block/plate temp | S = °C, H = hold seconds, V = sample volume |
| `M105` | Get block temp | replies `T:<target> C:<current> H:<hold>` |
| `M140 S<t>` | Set lid temp | |
| `M141` | Get lid temp | replies `T:<target> C:<current>` |
| `M119` | Get lid status | replies `Lid:<open\|closed\|in_between>` |
| `M126` / `M127` / `M128` | Open lid / Close lid / Plate lift | |
| `M14` / `M108` / `M18` | Deactivate block / lid / all | |

## Safety notes

- The block accepts roughly **4–99 °C**, the lid roughly **37–110 °C**; the GUI
  validates against these ranges before sending.
- **Close the lid before heating the block.** The app lets you open the lid at any
  time — don't open it during a run.
- Holds are timed in software (the app waits for the block to reach temperature,
  then times the hold), which is why the graph and countdown reflect actual block
  temperature rather than a fixed schedule.
- This is community software, not an Opentrons product. Test with the Simulator, and
  validate against your own protocol before trusting it with real samples.

# Opentrons Thermocycler Module GEN 1 — Desktop Control GUI

A standalone Python desktop app to control an **Opentrons Thermocycler Module (GEN 1)**
directly over USB serial — **no OT-2 / Flex robot required**. The module plugs
straight into your computer and is driven with its native G-code serial protocol.

![overview](docs_placeholder)

## What it does

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
- **Live graph** — block and lid temperature plotted over time.
- **Log** — every command sent and response received.

## Install

Requires Python 3.8+. Tkinter ships with most Python installs (on some Linux
distros: `sudo apt install python3-tk`).

```bash
pip install -r requirements.txt   # pyserial + matplotlib
```

## Run

```bash
python thermocycler_gui.py
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

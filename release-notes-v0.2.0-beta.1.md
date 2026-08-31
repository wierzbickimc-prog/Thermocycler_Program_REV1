# BUILT DNA Thermocycler Console v0.2.0-beta.1

Experiment-tracked runs, a full run-history archive, and redesigned PDF
reports for the standalone GEN 1 thermocycler console.

## Install on macOS

1. Download `BUILT-DNA-Thermocycler-macOS-v0.2.0-beta.1.zip` and its checksum.
2. Unzip it.
3. Control-click **BUILT DNA Thermocycler.app** and choose **Open**.
4. Approve the one-time private Python runtime download.

No Homebrew, Xcode, administrator password, or preinstalled Python is required.
The first launch needs an internet connection and stores its isolated runtime in
`~/Library/Application Support/BUILT DNA Thermocycler/`. Later launches work
without downloading it again. Keep the Terminal window open while using the
console; press Control-C there to stop it safely.

This package is ad-hoc signed but not Apple-notarized. It is a prerelease for
testing with Opentrons Thermocycler Module GEN 1 hardware. Validate protocols
with the simulator before using valuable samples.

## Highlights

- **LAB/LPD run ID** — starting a profile run requires the operator to type a
  LAB/LPD number; it is stored with the durable run record, shown on the
  control screen, and printed in the report header.
- **Run history** — a console-wide page lists every recorded run (newest
  first) with its LAB/LPD number, instrument, duration, step progress and
  status. Search by LAB/LPD number, profile or instrument, and download any
  run's PDF report straight from the list (meaningful filenames).
- **Redesigned PDF reports** — vector layout with a branded header, key/value
  summary, interruption-recovery callout, striped profile-steps table, a
  temperature trace chart (block and lid), and a clean event log.
- **Lid cools after the run** — once the block actually reaches the final
  cold-hold setpoint, the lid heater is switched off so the lid cools to room
  temperature; the event is recorded in the run log.
- **Run ETA on the control screen** — total remaining time (ramp time
  included) and estimated completion alongside the run progress bar.
- **More lid-confirm grace** — the lid-position wait before a run starts or
  resumes is 30 s instead of 20 s.

## Notes

- Existing installations keep their run database at `~/.builtdna/runs.sqlite3`;
  runs recorded before this release simply have no LAB/LPD number (shown as
  "—" in the history and reports).
- The PDF event log omits raw serial command lines (still visible live in the
  console Log tab) to keep reports compact.

## Install on macOS

1. Download `BUILT-DNA-Thermocycler-macOS-v0.1.0-beta.1.zip` and its checksum.
2. Unzip it.
3. Control-click **BUILT DNA Thermocycler.app** and choose **Open**.
4. Approve the one-time private Python runtime download.

No Homebrew, Xcode, administrator password, or preinstalled Python is required.
The first launch needs an internet connection and stores its isolated runtime in
`~/Library/Application Support/BUILT DNA Thermocycler/`. Later launches work
without downloading it again. Keep the Terminal window open while using the
console; press Control-C there to stop it safely.

This package is ad-hoc signed but not Apple-notarized. It is a prerelease for
testing with Opentrons Thermocycler Module GEN 1 hardware. Validate protocols
with the simulator before using valuable samples.

## Highlights

- Multi-instrument web console with simulated devices.
- Editable profiles with required open/closed lid position.
- Persistent activation controls for standard profiles.
- Hot and cold component glows.
- Total estimated time remaining and projected completion time.
- Thermal QC workflow and G-code descriptors.

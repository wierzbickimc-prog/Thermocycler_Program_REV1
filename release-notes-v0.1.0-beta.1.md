# BUILT DNA Thermocycler Console v0.1.0-beta.1

First public macOS test release of the standalone GEN 1 thermocycler console.

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

# macOS test package

`build_release.sh` creates a self-contained `.app` wrapper and zip archive in
`dist/`. The wrapper works on Apple Silicon and Intel Macs because its first-run
launcher selects the matching runtime architecture.

The first launch:

1. Downloads pinned CPython 3.12 into
   `~/Library/Application Support/BUILT DNA Thermocycler/`.
2. Verifies the runtime's SHA-256 checksum before installing it.
3. Installs the bundled and checksum-verified `pyserial` wheel without contacting
   PyPI.
4. Opens the local console at `http://127.0.0.1:8765`.

It does not require or modify Homebrew, Xcode, Command Line Tools, or the system
Python. The Terminal window remains open while the server is running; press
Control-C there to stop it safely.

Build with:

```bash
./macos/build_release.sh
```

The app is ad-hoc signed for integrity but not notarized. Testers should unzip
the release, then Control-click the app and choose **Open** on first launch.

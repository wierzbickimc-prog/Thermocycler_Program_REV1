#!/usr/bin/env python3
"""BUILT DNA thermocycler console - local web server.

Serves the single-page UI and a small JSON API over the standard library only
(no FastAPI/uvicorn), so the app runs in the same venv as the serial layer.
Live state reaches the browser over Server-Sent Events.

Run:
    python serve.py            # opens http://127.0.0.1:8765 in your browser
    python serve.py --port 9000 --no-browser
"""

import argparse
import json
import mimetypes
import queue
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import profiles as profile_lib
from devices import DeviceManager

WEB_DIR = Path(__file__).resolve().parent / "web"
MANAGER = None                      # set in main()


class Handler(BaseHTTPRequestHandler):
    server_version = "BuiltDNA/1.0"
    protocol_version = "HTTP/1.1"

    # -- plumbing ----------------------------------------------------------
    def log_message(self, fmt, *args):          # quieter console
        pass

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, data, code=200):
        self._send(code, json.dumps(data).encode(), "application/json")

    def _error(self, code, message):
        self._json({"error": message}, code)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    # -- routing -----------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/state":
                return self._json(self._state())
            if path == "/api/events":
                return self._events()
            if path == "/api/profiles":
                return self._json({"profiles": profile_lib.list_profiles()})
            if path.startswith("/api/device/"):
                dev_id = path[len("/api/device/"):]
                dev = MANAGER.get(dev_id)
                return self._json(dev.snapshot(with_history=True, with_log=True))
            return self._static(path)
        except KeyError as exc:
            self._error(404, f"Not found: {exc}")
        except Exception as exc:
            self._error(500, str(exc))

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/scan":
                found = MANAGER.scan()
                return self._json({"found": found, "devices": self._state()})
            if path == "/api/profiles":
                return self._json(profile_lib.save_profile(body))
            if path.startswith("/api/device/"):
                rest = path[len("/api/device/"):]
                dev_id, _, verb = rest.partition("/")
                dev = MANAGER.get(dev_id)
                if verb == "name":
                    MANAGER.rename(dev_id, body.get("name"))
                    return self._json(dev.snapshot())
                if verb == "action":
                    return self._json(self._action(dev, body))
            self._error(404, "Unknown endpoint")
        except KeyError as exc:
            self._error(404, f"Not found: {exc}")
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, str(exc))

    def do_DELETE(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/profiles/"):
                profile_lib.delete_profile(path[len("/api/profiles/"):])
                return self._json({"ok": True})
            self._error(404, "Unknown endpoint")
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, str(exc))

    # -- actions -----------------------------------------------------------
    def _action(self, dev, body):
        name = body.get("action")
        if name == "connect":
            dev.connect()
        elif name == "disconnect":
            dev.disconnect()
        elif name == "set_block":
            dev.action("set_block", temp=float(body["temp"]),
                       hold=body.get("hold"), volume=body.get("volume"))
        elif name == "set_lid":
            dev.action("set_lid", temp=float(body["temp"]))
        elif name in ("open_lid", "close_lid", "plate_lift",
                      "deactivate_block", "deactivate_lid", "deactivate_all",
                      "stop_run"):
            dev.action(name)
        elif name == "run_profile":
            prof = body.get("profile")
            if prof is None:
                prof = profile_lib.get_profile(body["profile_id"])
            else:
                prof = profile_lib.validate_profile(prof)
            dev.run_profile(prof)
        else:
            raise ValueError(f"Unknown action: {name}")
        return dev.snapshot()

    # -- state / streaming -------------------------------------------------
    @staticmethod
    def _state():
        return {"devices": [d.snapshot() for d in MANAGER.devices()]}

    def _events(self):
        """Server-Sent Events: a coalesced state push on every device change."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        sub = MANAGER.subscribe()
        try:
            self._push(self._state())
            last = 0.0
            while True:
                try:
                    sub.get(timeout=2.0)
                    got = True
                except queue.Empty:
                    got = False
                if got:
                    # Coalesce a burst. This drain MUST NOT share an except
                    # clause with the blocking get above - doing so routed every
                    # real event into the heartbeat branch and froze the UI at
                    # whatever state it held when the page loaded.
                    while True:
                        try:
                            sub.get_nowait()
                        except queue.Empty:
                            break
                else:
                    # Heartbeat comment. Keeps proxies from idling the stream out
                    # and, more importantly, makes a client that navigated away
                    # raise BrokenPipe within ~2s instead of lingering. A browser
                    # only allows ~6 connections per host, so a leaked stream per
                    # reload will otherwise lock the app out of its own API.
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                now = time.time()
                if now - last < 0.1:
                    time.sleep(0.1 - (now - last))
                last = time.time()
                self._push(self._state())
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            MANAGER.unsubscribe(sub)

    def _push(self, data):
        self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    # -- static files ------------------------------------------------------
    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        if not str(target).startswith(str(WEB_DIR)) or not target.is_file():
            target = WEB_DIR / "index.html"       # SPA fallback for #/ routes
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)


def main():
    global MANAGER
    ap = argparse.ArgumentParser(description="BUILT DNA thermocycler console")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-scan", action="store_true",
                    help="skip probing serial ports at startup")
    args = ap.parse_args()

    MANAGER = DeviceManager()
    if not args.no_scan:
        threading.Thread(target=MANAGER.scan, daemon=True).start()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    url = f"http://{args.host}:{args.port}"
    print(f"BUILT DNA console  ->  {url}")
    print("Ctrl-C to stop.")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        MANAGER.shutdown()


if __name__ == "__main__":
    main()

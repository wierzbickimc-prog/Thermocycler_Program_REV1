"""Unit tests for the pure protocol helpers (no hardware / GUI needed)."""
import thermocycler_core as tc
import profiles as profile_lib
import devices as device_lib
import run_history


def test_build_set_block_temp_only():
    assert tc.build_set_block(95) == "M104 S95.00"


def test_build_set_block_with_hold_and_volume():
    assert tc.build_set_block(98.5, hold_s=120, volume_uL=50) == "M104 S98.50 H120 V50.0"


def test_build_set_lid():
    assert tc.build_set_lid(105) == "M140 S105.00"


def test_describe_gcode():
    assert tc.describe_gcode("M104 S95.00 V25.0") == "Set block temperature"
    assert tc.describe_gcode("M126") == "Open lid"
    assert tc.describe_gcode("G999") is None


def _minimal_profile(**options):
    return {
        "name": "test",
        "stages": [{"name": "hold", "cycles": 1,
                    "steps": [{"temp": 25, "seconds": 10}]}],
        **options,
    }


def test_profile_lid_position_defaults_closed():
    assert profile_lib.validate_profile(_minimal_profile())["lid_position"] == "closed"


def test_open_lid_profile_disables_heated_lid():
    prof = profile_lib.validate_profile(_minimal_profile(
        lid_position="open", lid_temp=105, preheat_lid=True))
    assert prof["lid_position"] == "open"
    assert prof["lid_temp"] is None and prof["preheat_lid"] is False


def test_profile_rejects_unknown_lid_position():
    try:
        profile_lib.validate_profile(_minimal_profile(lid_position="ajar"))
    except ValueError as exc:
        assert "lid_position" in str(exc)
    else:
        raise AssertionError("invalid lid position was accepted")


def test_builtin_profile_activation_is_persisted():
    import tempfile
    from pathlib import Path
    original = profile_lib.SETTINGS_FILE
    try:
        with tempfile.TemporaryDirectory() as tmp:
            profile_lib.SETTINGS_FILE = Path(tmp) / "profile_settings.json"
            changed = profile_lib.set_builtin_active("std-3step", False)
            assert changed["active"] is False
            listed = {p["id"]: p for p in profile_lib.list_profiles()}
            assert listed["std-3step"]["active"] is False
            profile_lib.set_builtin_active("std-3step", True)
            assert profile_lib.get_profile("std-3step")["active"] is True
    finally:
        profile_lib.SETTINGS_FILE = original


def test_user_profile_cannot_use_builtin_activation_setting():
    try:
        profile_lib.set_builtin_active("user:custom", False)
    except ValueError as exc:
        assert "standard profiles" in str(exc)
    else:
        raise AssertionError("user profile accepted by built-in activation setting")


def test_parse_plate_response():
    cur, tgt = tc.parse_temperature("T:none C:25.0 H:123")
    assert cur == 25.0 and tgt is None


def test_parse_lid_temp_response():
    cur, tgt = tc.parse_temperature("T:105.00 C:98.4")
    assert cur == 98.4 and tgt == 105.0


def test_parse_lid_status_variants():
    assert tc.parse_lid_status("Lid:open") == "open"
    assert tc.parse_lid_status("Lid:closed") == "closed"
    assert tc.parse_lid_status("Lid:in_between") == "in_between"
    assert tc.parse_lid_status("garbage") == "unknown"


def test_key_values_mixed():
    kv = tc.parse_key_values("T:none C:4.00 H:none")
    assert kv["C"] == "4.00" and kv["T"].lower() == "none"


def test_flatten_profile_cycles():
    stages = [
        {"name": "Denat", "cycles": 1, "steps": [{"temp": 95, "seconds": 180}]},
        {"name": "Cyc", "cycles": 3, "steps": [
            {"temp": 95, "seconds": 15},
            {"temp": 60, "seconds": 30},
        ]},
        {"name": "Hold", "cycles": 1, "steps": [{"temp": 4, "seconds": 0}]},
    ]
    flat = tc.flatten_profile(stages)
    # 1 + (2*3) + 1 = 8 steps
    assert len(flat) == 8
    assert flat[0]["seconds"] == 180
    assert flat[-1]["seconds"] is None          # 0 -> indefinite hold
    assert "cycle 2/3" in flat[3]["label"]
    assert flat[3]["cycle"] == 2 and flat[3]["cycles"] == 3


def test_run_eta_includes_future_holds_and_ramps():
    steps = [
        {"label": "hot", "temp": 95.0, "seconds": 60},
        {"label": "anneal", "temp": 55.0, "seconds": 30},
        {"label": "final hold", "temp": 4.0, "seconds": None},
    ]
    remaining, kind = device_lib.estimate_run_remaining(
        steps, index=0, phase="hold", step_remaining=20,
        block_current=95.0, simulated=True)
    assert remaining == 73
    assert kind == "final_hold"


def test_run_eta_accounts_for_lid_preheat():
    steps = [{"label": "step", "temp": 25.0, "seconds": 10}]
    remaining, kind = device_lib.estimate_run_remaining(
        steps, phase="preheat", block_current=23.0,
        lid_current=23.0, lid_target=105.0, simulated=True)
    assert remaining == 43
    assert kind == "complete"


def test_run_eta_marks_terminal_indefinite_hold_reached():
    steps = [{"label": "final hold", "temp": 4.0, "seconds": None}]
    assert device_lib.estimate_run_remaining(
        steps, phase="hold_inf", block_current=4.0) == (0, "final_hold")


def test_simulator_roundtrip():
    import time
    sim = tc.SimulatorTransport()
    start_cur, _ = tc.parse_temperature(sim.send("M105"))
    sim.send("M104 S95")
    time.sleep(0.5)  # let real wall-clock time elapse so the model ramps
    cur, tgt = tc.parse_temperature(sim.send("M105"))
    assert tgt == 95.0                     # target registered
    assert cur > start_cur                 # ramping upward toward target
    assert tc.parse_lid_status(sim.send("M119")) in ("open", "closed", "in_between")


def test_simulator_lid_movement():
    sim = tc.SimulatorTransport()
    sim.send("M126")  # open
    assert sim.lid_status == "in_between"


# ---------------------------------------------------------------------------
#  Runner regression tests - each covers a bug that shipped in the first cut
# ---------------------------------------------------------------------------
def _worker(steps, lid_temp=None, preheat=False):
    """A Worker wired to a simulator, with a profile already started."""
    import queue
    w = tc.Worker(queue.Queue())
    w._transport = tc.SimulatorTransport()
    w._start_run(steps, 25.0, lid_temp, preheat)
    return w


def _drain(w):
    out = []
    while not w.out_q.empty():
        out.append(w.out_q.get())
    return out


def test_profile_without_lid_temp_turns_heated_lid_off():
    """An open-lid profile must not inherit an earlier heated-lid target."""
    import queue
    w = tc.Worker(queue.Queue())
    w._transport = tc.SimulatorTransport()
    w._transport.lid_target = 90.0
    w._start_run([{"label": "s", "temp": 25.0, "seconds": 10}],
                 25.0, None, False)
    assert w._transport.lid_target is None


def test_ramp_timeout_keeps_indefinite_hold():
    """A ramp timeout must not turn 'hold forever' into 'skip this step'."""
    import time
    w = _worker([{"label": "4C hold", "temp": 4.0, "seconds": None}])
    w._phase = "ramp"
    w._ramp_deadline = time.time() - 1          # force the timeout
    w._tick_run()
    assert w._hold_end is None, "indefinite hold lost its None sentinel"
    w._tick_run()
    assert w._run_steps is not None, "step was skipped instead of held"


def test_ramp_timeout_emits_progress():
    """The timeout path must still tell the GUI what phase it is in."""
    import time
    w = _worker([{"label": "s", "temp": 95.0, "seconds": 30}])
    w._phase = "ramp"
    w._ramp_deadline = time.time() - 1
    _drain(w)
    w._tick_run()
    assert any(m["kind"] == "run_step" for m in _drain(w))


def test_preheat_waits_for_lid():
    """With preheat on, no block command may go out until the lid is hot."""
    w = _worker([{"label": "s", "temp": 95.0, "seconds": 10}],
                lid_temp=105.0, preheat=True)
    assert w._phase == "preheat"
    for _ in range(5):
        w._tick_run()
    assert w._transport.block_target is None, "block heated before lid was ready"
    w._transport.lid_current = 105.0           # lid arrives
    w._lid_current, w._lid_at = None, 0.0      # invalidate the cache
    w._tick_run()
    assert w._phase is None, "runner did not leave preheat once lid was hot"


def test_preheat_off_starts_immediately():
    w = _worker([{"label": "s", "temp": 95.0, "seconds": 10}],
                lid_temp=105.0, preheat=False)
    assert w._phase is None
    w._tick_run()
    assert w._transport.block_target == 95.0


def test_stop_deactivates_heaters():
    """The Stop button must turn the heaters off, not just stop the timer."""
    w = _worker([{"label": "s", "temp": 95.0, "seconds": 600}])
    w._tick_run()
    assert w._transport.block_target == 95.0
    w._handle("stop_run", {})                  # exactly what the button submits
    assert w._transport.block_target is None
    assert w._transport.lid_target is None


def test_stop_while_idle_is_silent():
    """Stopping with nothing running must not report a phantom 'Stopped'."""
    import queue
    w = tc.Worker(queue.Queue())
    w._transport = tc.SimulatorTransport()
    w._handle("stop_run", {})
    assert not any(m["kind"] == "run_finished" for m in _drain(w))


def test_hold_phase_issues_no_serial_traffic():
    """Holding needs no temperature reads - the module maintains the target."""
    class Counting(tc.SimulatorTransport):
        def __init__(self):
            super().__init__()
            self.calls = []

        def send(self, cmd, timeout=None):
            self.calls.append(cmd.split()[0])
            return super().send(cmd, timeout)

    import queue, time
    w = tc.Worker(queue.Queue())
    w._transport = Counting()
    w._start_run([{"label": "h", "temp": 23.0, "seconds": 600}], 25.0, None, False)
    w._tick_run()                              # issues the M104
    w._transport.calls.clear()
    w._block_current, w._block_at = 23.0, time.time()
    w._phase = "hold"
    w._hold_end = time.time() + 600
    for _ in range(50):
        w._tick_run()
    assert w._transport.calls == [], f"hold polled the device: {w._transport.calls}"


def test_worker_is_joinable():
    """Worker must not shadow Thread._stop, or join() raises TypeError."""
    import queue
    w = tc.Worker(queue.Queue())
    w.start()
    w.shutdown()
    w.join(timeout=3.0)
    assert not w.is_alive()


# ---------------------------------------------------------------------------
#  Power-loss recovery and durable reporting
# ---------------------------------------------------------------------------
def test_serial_transport_requires_acknowledgement():
    """An unplugged module must not turn an empty serial read into success."""
    class SilentSerial:
        def reset_input_buffer(self): pass
        def write(self, _data): pass
        def flush(self): pass
        def read(self, _count): return b""

    transport = object.__new__(tc.SerialTransport)
    transport._ser = SilentSerial()
    transport._timeout = 0.01
    try:
        transport.send("M105")
    except TimeoutError as exc:
        assert "No acknowledgement" in str(exc)
    else:
        raise AssertionError("silent serial connection was accepted")


def test_communication_loss_emits_resumable_checkpoint():
    import time

    class FailedTransport:
        def send(self, _command, timeout=None):
            raise OSError("USB device disappeared")
        def close(self): pass

    w = _worker([{"label": "hold", "temp": 25.0, "seconds": 60}])
    _drain(w)
    w._phase = "hold"
    w._hold_end = time.time() + 42
    w._transport = FailedTransport()
    assert w._send("M105") is None
    events = _drain(w)
    checkpoint = next(m for m in events if m["kind"] == "run_interrupted")
    assert checkpoint["index"] == 0 and checkpoint["phase"] == "hold"
    assert 40 <= checkpoint["remaining"] <= 42
    disconnected = next(m for m in events if m["kind"] == "disconnected")
    assert disconnected["unexpected"] is True


def test_resume_reramps_then_uses_saved_hold_remaining():
    import queue
    import time

    steps = [
        {"label": "first", "temp": 95.0, "seconds": 30},
        {"label": "second", "temp": 25.0, "seconds": 60},
    ]
    w = tc.Worker(queue.Queue())
    w._transport = tc.SimulatorTransport()
    w._transport.block_current = 25.0
    w._start_run(steps, 25.0, None, False, resume_index=1,
                 resume_phase="hold", resume_remaining=7)
    w._tick_run()                 # reissue the current step's setpoint
    assert w._phase == "ramp" and w._transport.block_target == 25.0
    w._block_current, w._block_at = 25.0, time.time()
    w._tick_run()                 # at temperature: restore the saved dwell
    assert w._phase == "hold"
    assert 6.5 <= w._hold_end - time.time() <= 7.0


def test_run_store_recovers_checkpoint_and_builds_pdf():
    import tempfile
    from pathlib import Path

    profile = {
        "name": "Recovery test", "lid_position": "closed",
        "stages": [{"name": "Hold", "cycles": 1,
                    "steps": [{"temp": 25.0, "seconds": 60}]}],
    }
    steps = tc.flatten_profile(profile["stages"])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "runs.sqlite3"
        store = run_history.RunStore(path)
        run_id = store.start_run("dev-1", "Cycler 1", False, profile, steps)
        store.update_progress(run_id, 0, 1, "hold", 37)
        store.add_telemetry(run_id, {
            "t": 1234.0, "block_current": 25.0, "block_target": 25.0,
            "lid_current": 100.0, "lid_target": 105.0,
            "lid_status": "closed",
        })
        store.close()             # process ended without a run_finished event

        recovered = run_history.RunStore(path)
        checkpoint = recovered.latest_resumable("dev-1")
        assert checkpoint["id"] == run_id
        assert checkpoint["phase"] == "hold" and checkpoint["remaining_s"] == 37
        assert checkpoint["interrupted_step_index"] == 0
        detailed = recovered.get_run(run_id, details=True)
        pdf = run_history.render_run_pdf(detailed)
        assert pdf.startswith(b"%PDF-1.4") and pdf.endswith(b"%%EOF\n")
        assert b"Recovery test" in pdf
        resumed_at = recovered.resume_run(run_id, automatic=True)
        resumed = recovered.get_run(run_id)
        assert resumed["resumed_at"] == resumed_at
        assert resumed["resume_automatic"] is True
        resumed_pdf = run_history.render_run_pdf(
            recovered.get_run(run_id, details=True))
        assert b"Resumed automatically" in resumed_pdf
        recovered.close()


def test_device_disconnect_reconnect_resume_end_to_end():
    import tempfile
    import time
    from pathlib import Path

    def wait_for(predicate, timeout=4.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        raise AssertionError("timed out waiting for device state")

    with tempfile.TemporaryDirectory() as tmp:
        store = run_history.RunStore(Path(tmp) / "runs.sqlite3")
        dev = device_lib.Device("test-sim", "Recovery simulator", None, True,
                                lambda _id: None, store)
        try:
            dev.connect()
            wait_for(lambda: dev.snapshot()["connected"])
            profile = {
                "name": "End-to-end recovery", "lid_position": "closed",
                "lid_temp": None, "preheat_lid": False, "volume": 25.0,
                "stages": [{"name": "Cycling", "cycles": 3,
                            "steps": [{"temp": 23.0, "seconds": 60}]}],
            }
            dev.run_profile(profile)
            wait_for(lambda: dev.snapshot()["run_phase"] == "hold")
            run_id = dev.snapshot()["latest_run_id"]

            dev.disconnect()
            wait_for(lambda: dev.snapshot()["resume_available"])
            assert store.get_run(run_id)["status"] == "interrupted"

            dev.connect()
            wait_for(lambda: dev.snapshot()["connected"])
            wait_for(lambda: dev.snapshot()["running"], timeout=5.0)
            notice = dev.snapshot()["recovery_notice"]
            assert notice["automatic"] is True
            assert notice["resumed_at"] >= notice["lost_at"]
            assert notice["cycle"] == 1 and notice["cycles"] == 3
            dev.action("stop_run")
            wait_for(lambda: store.get_run(run_id)["status"] == "stopped")
            assert store.get_run(run_id)["resume_count"] == 1
        finally:
            dev.shutdown()
            store.close()


if __name__ == "__main__":
    import sys
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in funcs:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {fn.__name__}: {e!r}")
    print(f"\n{len(funcs) - failed}/{len(funcs)} passed")
    sys.exit(1 if failed else 0)

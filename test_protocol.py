"""Unit tests for the pure protocol helpers (no hardware / GUI needed)."""
import thermocycler_core as tc


def test_build_set_block_temp_only():
    assert tc.build_set_block(95) == "M104 S95.00"


def test_build_set_block_with_hold_and_volume():
    assert tc.build_set_block(98.5, hold_s=120, volume_uL=50) == "M104 S98.50 H120 V50.0"


def test_build_set_lid():
    assert tc.build_set_lid(105) == "M140 S105.00"


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

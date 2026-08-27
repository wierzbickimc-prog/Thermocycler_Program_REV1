"""Safety checks for managed device actions."""

import threading

import pytest

from devices import Device


class RecordingWorker:
    def __init__(self):
        self.calls = []

    def submit(self, action, **kwargs):
        self.calls.append((action, kwargs))


def make_device(lid_status, supports_plate_lift=True):
    """Build the action-facing portion of a Device without starting threads."""
    device = Device.__new__(Device)
    device._lock = threading.Lock()
    device.lid_status = lid_status
    device.supports_plate_lift = supports_plate_lift
    device.lid_moving_to = None
    device.worker = RecordingWorker()
    return device


@pytest.mark.parametrize("lid_status", ["closed", "in_between", "unknown"])
def test_plate_lift_rejected_unless_lid_is_open(lid_status):
    device = make_device(lid_status)

    with pytest.raises(ValueError, match="lid is fully open"):
        device.action("plate_lift")

    assert device.worker.calls == []


def test_plate_lift_allowed_with_open_lid():
    device = make_device("open")

    device.action("plate_lift")

    assert device.worker.calls == [("plate_lift", {})]


def test_plate_lift_rejected_for_gen1_even_with_open_lid():
    device = make_device("open", supports_plate_lift=False)

    with pytest.raises(ValueError, match="only available on GEN2"):
        device.action("plate_lift")

    assert device.worker.calls == []

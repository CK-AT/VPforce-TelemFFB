"""Read DIY gateway/axis USB-HID gamepad buttons for DiyFfbDevice.get_input().

The rig's buttons ride the ESP32s' **native-USB HID gamepads** (VID 0x303b,
product "DIY-FFB-*") — a separate interface from the UART-bridge FFB link. Each
ESP32 (gateway and each axis controller) presents its own gamepad, and the grip
may be wired to any of them, so this opens **all** DIY gamepads and **ORs** their
button bitfields into one 48-button space. TelemFFB then sees buttons as if from
a single device (like a VPforce Rhino).

Per-device report layout (Joystick_ESP32S3 gamepad, 0 hats, 48 buttons):

    [ reportId=3 | button bytes (ceil(48/8)=6, little-endian, bit0 = button 1) | axes… ]

No firmware change: `CommManager` already folds the grip shift-register bits
into these buttons (indices 24–47) and sends them on the gamepad.
"""

from __future__ import annotations

import logging
import threading
from functools import reduce
from operator import or_
from typing import Callable, Dict, List, Optional

log = logging.getLogger("diy_buttons")

DIY_VID = 0x303B
REPORT_ID = 3
BUTTON_COUNT = 48
BUTTON_BYTES = (BUTTON_COUNT + 7) // 8   # 6
PRODUCT_PREFIX = "DIY-FFB-"
USAGE_PAGE_GENERIC_DESKTOP = 0x01
USAGE_GAMEPAD = 0x05
READ_SIZE = 64


def decode_buttons(report: bytes) -> Optional[int]:
    """HID input report -> button bitfield (bit 0 == button 1).

    Returns None when the report isn't the gamepad's numbered report (so a
    stray report never gets mistaken for "all buttons released").
    """
    if len(report) < 1 + BUTTON_BYTES or report[0] != REPORT_ID:
        return None
    return int.from_bytes(report[1:1 + BUTTON_BYTES], "little")


def find_gamepad_paths() -> List[bytes]:
    """hidapi paths of every DIY gamepad interface (gateway + axes).

    Prefer the generic-desktop/gamepad usage; fall back to the product-name
    prefix only if no interface reports usage (some HID stacks omit it).
    """
    from telemffb.hw import hid  # lazy: needs hidapi.dll (present on the rig PC)
    by_usage: List[bytes] = []
    by_name: List[bytes] = []
    for d in hid.enumerate(DIY_VID, 0):
        path = d.get("path")
        if not path:
            continue
        if d.get("usage_page") == USAGE_PAGE_GENERIC_DESKTOP and d.get("usage") == USAGE_GAMEPAD:
            by_usage.append(path)
        elif (d.get("product_string") or "").startswith(PRODUCT_PREFIX):
            by_name.append(path)
    paths = by_usage or by_name
    # dedupe, preserve order
    seen = set()
    return [p for p in paths if not (p in seen or seen.add(p))]


class GripButtonReader:
    """Polls every DIY gamepad HID on background threads and exposes the OR of
    their button bitfields, with an on-change callback for edge events."""

    def __init__(self, paths: Optional[List[bytes]] = None,
                 on_change: Optional[Callable[[int, int], None]] = None):
        self._paths = paths
        self._on_change = on_change
        self._values: Dict[bytes, int] = {}   # per-device latest bitfield
        self._combined = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._devs = []
        self._threads: List[threading.Thread] = []

    def start(self) -> None:
        try:
            from telemffb.hw import hid  # lazy: needs hidapi.dll
            paths = self._paths or find_gamepad_paths()
            if not paths:
                log.warning("no DIY gamepad HID found (VID %04X) — buttons unavailable", DIY_VID)
                return
            for path in paths:
                try:
                    dev = hid.Device(path=path)
                except Exception:  # noqa: BLE001
                    log.exception("failed to open DIY gamepad %r", path)
                    continue
                self._devs.append(dev)
                t = threading.Thread(target=self._loop, args=(path, dev),
                                     name="diy-buttons", daemon=True)
                t.start()
                self._threads.append(t)
            log.info("grip button reader started on %d gamepad(s)", len(self._devs))
        except Exception:  # noqa: BLE001
            log.exception("failed to start grip button reader")

    def _loop(self, key: bytes, dev) -> None:
        while not self._stop.is_set():
            try:
                data = dev.read(READ_SIZE, timeout=50)
            except Exception:  # noqa: BLE001
                if not self._stop.is_set():
                    log.exception("grip button read failed on %r", key)
                break
            if not data:
                continue
            value = decode_buttons(data)
            if value is not None:
                self._update(key, value)

    def _update(self, key: bytes, value: int) -> None:
        """Record one device's bitfield; recompute the OR; fire on-change if the
        combined value moved. Callback runs outside the lock."""
        fire = None
        with self._lock:
            if self._values.get(key, 0) == value:
                return
            self._values[key] = value
            new = reduce(or_, self._values.values(), 0)
            if new != self._combined:
                fire = (self._combined, new)
                self._combined = new
        if fire and self._on_change:
            self._on_change(*fire)

    def buttons(self) -> int:
        with self._lock:
            return self._combined

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=1.0)
        for dev in self._devs:
            try:
                dev.close()
            except Exception:  # noqa: BLE001
                pass

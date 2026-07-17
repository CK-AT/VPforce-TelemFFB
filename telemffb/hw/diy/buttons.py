"""Read the DIY gateway's USB-HID gamepad buttons for DiyFfbDevice.get_input().

The rig's grip buttons ride the gateway's **native-USB HID gamepad** (VID
0x303b, product "DIY-FFB-*") — a separate interface from the UART-bridge FFB
link. TelemFFB expects the FFB device itself to report buttons (like a VPforce
Rhino), so we poll that gamepad here and hand its button bitfield to
`DiyFfbDevice`, which merges it into the `FFBReport_Input` it returns.

Report layout (Joystick_ESP32S3 gamepad, 0 hat switches, 48 buttons):

    [ reportId=3 | button bytes (ceil(48/8)=6, little-endian, bit0 = button 1) | axes… ]

No firmware change: `CommManager` already folds the grip shift-register bits
into these buttons (indices 24–47) and sends them on this gamepad.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("diy_buttons")

DIY_VID = 0x303B
REPORT_ID = 3
BUTTON_COUNT = 48
BUTTON_BYTES = (BUTTON_COUNT + 7) // 8   # 6
PRODUCT_PREFIX = "DIY-FFB-"
USAGE_PAGE_GENERIC_DESKTOP = 0x01
USAGE_GAMEPAD = 0x05
READ_SIZE = 64


def decode_buttons(report: bytes) -> int:
    """HID input report -> button bitfield (bit 0 == button 1).

    Returns 0 for a report that isn't the gamepad's numbered report.
    """
    if len(report) < 1 + BUTTON_BYTES or report[0] != REPORT_ID:
        return 0
    return int.from_bytes(report[1:1 + BUTTON_BYTES], "little")


def find_gamepad_path() -> Optional[bytes]:
    """hidapi path of the DIY gateway gamepad interface, or None.

    Prefer the generic-desktop/gamepad usage; fall back to the product-name
    prefix so it still works if usage fields aren't populated.
    """
    from telemffb.hw import hid  # lazy: needs hidapi.dll (present on the rig PC)
    fallback = None
    for d in hid.enumerate(DIY_VID, 0):
        if d.get("usage_page") == USAGE_PAGE_GENERIC_DESKTOP and d.get("usage") == USAGE_GAMEPAD:
            return d.get("path")
        if (d.get("product_string") or "").startswith(PRODUCT_PREFIX):
            fallback = fallback or d.get("path")
    return fallback


class GripButtonReader:
    """Polls the DIY gamepad HID on a background thread; exposes the latest
    button bitfield and fires an on-change callback for edge events."""

    def __init__(self, path: Optional[bytes] = None,
                 on_change: Optional[Callable[[int, int], None]] = None):
        self._path = path
        self._on_change = on_change
        self._buttons = 0
        self._dev = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        try:
            from telemffb.hw import hid  # lazy: needs hidapi.dll
            path = self._path or find_gamepad_path()
            if not path:
                log.warning("DIY gamepad HID not found (VID %04X) — buttons unavailable", DIY_VID)
                return
            self._dev = hid.Device(path=path)
            self._thread = threading.Thread(target=self._loop, name="diy-buttons", daemon=True)
            self._thread.start()
            log.info("grip button reader started")
        except Exception:  # noqa: BLE001
            log.exception("failed to start grip button reader")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._dev.read(READ_SIZE, timeout=50)
            except Exception:  # noqa: BLE001
                if not self._stop.is_set():
                    log.exception("grip button read failed")
                break
            if not data:
                continue
            new = decode_buttons(data)
            old = self._buttons
            if new != old:
                self._buttons = new
                if self._on_change:
                    try:
                        self._on_change(old, new)
                    except Exception:  # noqa: BLE001
                        log.exception("button on_change callback raised")

    def buttons(self) -> int:
        return self._buttons

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._dev:
            try:
                self._dev.close()
            except Exception:  # noqa: BLE001
                pass

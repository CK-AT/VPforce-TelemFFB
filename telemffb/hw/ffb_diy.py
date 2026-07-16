"""DIY FFB pedal/stick backend for TelemFFB.

Presents an ``FFBRhino``-compatible device object (assignable to
``HapticEffect.device``) that, underneath, speaks our COBS/protobuf protocol to
the ESP32 gateway via the serial broker. The whole TelemFFB effects engine,
mixins and aircraft profiles run unmodified — see
``DIY-Sim-Racing-FFB-Pedal/docs/plans/26_telemffb_python_backend.md``.

Integration seam (kept minimal): TelemFFB's ``HapticEffect`` and
``FFBEffectHandle`` are REUSED as-is. Every effect update funnels through
``FFBEffectHandle`` -> ``device.write(bytes(FFBReport_*))``, so this device only
needs to:
  * ``create_effect(type)``  -> allocate an id, return a real ``FFBEffectHandle``
  * ``write(data)``          -> parse the report struct, update the aggregator
  * ``get_input()``          -> populate a real ``FFBReport_Input`` from telemetry
plus housekeeping (info/serial/reset_effects/get_firmware_version) and the Qt
signals main.py connects.

The pure effect->FlightFfbAction aggregation, transport, discovery and
axis-state tracking live in the repo-side ``diy_ffb_bridge`` package.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Dict, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from telemffb.hw.ffb_rhino import (
    DeviceInfo,
    EFFECT_CONSTANT,
    FFBEffectHandle,
    FFBReport_EffectOperation,
    FFBReport_Input,
    FFBReport_SetConstantForce,
    FFBReport_SetCondition,
    FFBReport_SetEffect,
    FFBReport_SetPeriodic,
    HID_REPORT_ID_BLOCK_FREE,
    HID_REPORT_ID_EFFECT_OPERATION,
    HID_REPORT_ID_SET_CONDITION,
    HID_REPORT_ID_SET_CONSTANT_FORCE,
    HID_REPORT_ID_SET_EFFECT,
    HID_REPORT_ID_SET_PERIODIC,
    OP_STOP,
    PERIODIC_EFFECTS,
)

# --- locate the repo-side bridge package ------------------------------------
_BRIDGE = os.environ.get("DIY_FFB_BRIDGE") or os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "DIY-Sim-Racing-FFB-Pedal", "tools", "diy_ffb_bridge",
)
if _BRIDGE not in sys.path:
    sys.path.insert(0, os.path.abspath(_BRIDGE))

import diy_ffb_protocol_pb2 as pb  # noqa: E402
from aggregator import AxisScale, EffectAggregator  # noqa: E402
from client import DiyFfbLink  # noqa: E402

log = logging.getLogger("ffb_diy")

# TelemFFB device-type -> the DIY functions it drives (plan 26 §6.1).
# 'x' -> roll / primary single axis, 'y' -> pitch.
ROLE_FUNCTIONS = {
    "joystick":   {"x": pb.FUNCTION_ID_FLIGHT_STICK_ROLL,
                   "y": pb.FUNCTION_ID_FLIGHT_STICK_PITCH},
    "collective": {"x": pb.FUNCTION_ID_FLIGHT_STICK_COLLECTIVE},
    "pedals":     {"x": pb.FUNCTION_ID_FLIGHT_PEDALS},
}

DEFAULT_MAX_FORCE_N = 60.0     # normalized load [-1..1] -> N; calibrate on HW
SEND_INTERVAL_MS = 4           # ~250 Hz downlink tick


class DiyFfbDevice(QObject):
    """FFBRhino-compatible device backed by the DIY serial broker."""

    buttonPressed = pyqtSignal(int)
    buttonReleased = pyqtSignal(int)
    deviceConnected = pyqtSignal(bool)

    def __init__(self, device_type: str = "joystick",
                 host: str = "127.0.0.1", port: int = 45111,
                 max_force_n: float = DEFAULT_MAX_FORCE_N):
        super().__init__()
        self._role = device_type if device_type in ROLE_FUNCTIONS else "joystick"
        self._assignments_fn = ROLE_FUNCTIONS[self._role]
        self._max_force_n = max_force_n

        self._agg = EffectAggregator()
        self._effect_types: Dict[int, int] = {}   # effect_id -> type
        self._effect_dirs: Dict[int, float] = {}   # effect_id -> last direction (deg)
        self._next_id = 1

        self._link = DiyFfbLink()
        self._link.client._addr = (host, port)

        # Neutral placeholders; the real identity is filled from the gateway's
        # DeviceInfo protobuf on discovery (_on_device_info). This is a synthetic
        # descriptor for TelemFFB — NOT the ESP32's native USB-HID descriptor,
        # which this serial/broker backend does not touch.
        self.info = DeviceInfo(
            interface_number=0, manufacturer_string="DIY FFB", path=b"diy://broker",
            product_id=0, product_string=f"DIY FFB ({self._role})", release_number=0,
            serial_number="", usage=0, usage_page=0, vendor_id=0x303b,
        )
        self._fw_version = "unknown"

        self._send_timer = QTimer(self)
        self._send_timer.setInterval(SEND_INTERVAL_MS)
        self._send_timer.timeout.connect(self._tick)

    # --- lifecycle ---------------------------------------------------------
    def open(self) -> "DiyFfbDevice":
        self._link.start()
        self._link.client.on("device_info", self._on_device_info)
        # Discovery is event-driven: DiyFfbLink probes each axis as GatewayState
        # reveals it. Request DeviceInfo up front for the identity fields.
        req = pb.Message()
        req.device_info_request.gateway_id = pb.GATEWAY_ID_1
        self._link.client.send(req)
        self._send_timer.start()
        self.deviceConnected.emit(True)
        log.info("DiyFfbDevice open (role=%s)", self._role)
        return self

    def close(self):
        self._send_timer.stop()
        self._link.client.close()
        self.deviceConnected.emit(False)

    @property
    def serial(self):
        return self.info.serial_number

    def get_firmware_version(self, cached=True) -> str:
        return self._fw_version

    def reset_effects(self):
        self._agg.clear()
        self._effect_types.clear()
        self._effect_dirs.clear()

    # --- effect allocation (HapticEffect calls this) ----------------------
    def create_effect(self, type) -> FFBEffectHandle:
        effect_id = self._next_id
        self._next_id += 1
        self._effect_types[effect_id] = type
        self._agg.ensure(effect_id, type)
        return FFBEffectHandle(self, effect_id, type)

    # --- report sink (FFBEffectHandle writes here) ------------------------
    def write(self, data):
        if not data:
            return
        report_id = data[0]
        try:
            self._dispatch(report_id, data)
        except Exception as e:  # noqa: BLE001
            log.debug("write parse error (report %d): %s", report_id, e)

    def _dispatch(self, report_id: int, data: bytes):
        if report_id == HID_REPORT_ID_SET_EFFECT:
            r = FFBReport_SetEffect.from_buffer_copy(data)
            self._effect_dirs[r.effectBlockIndex] = r.directionX * 360.0 / 255.0
        elif report_id == HID_REPORT_ID_SET_CONDITION:
            r = FFBReport_SetCondition.from_buffer_copy(data)
            etype = self._effect_types.get(r.effectBlockIndex, 0)
            self._agg.set_condition(
                r.effectBlockIndex, etype, r.parameterBlockOffset,
                coef=r.positiveCoefficient / 4096.0,
                cp_offset=r.cpOffset / 4096.0,
            )
        elif report_id == HID_REPORT_ID_SET_CONSTANT_FORCE:
            r = FFBReport_SetConstantForce.from_buffer_copy(data)
            self._agg.set_constant(
                r.effectBlockIndex, magnitude=r.magnitude / 4096.0,
                direction_deg=self._effect_dirs.get(r.effectBlockIndex, 0.0),
            )
        elif report_id == HID_REPORT_ID_SET_PERIODIC:
            r = FFBReport_SetPeriodic.from_buffer_copy(data)
            freq = 1000.0 / r.period if r.period else 0.0
            self._agg.set_periodic(
                r.effectBlockIndex, self._effect_types.get(r.effectBlockIndex, 0),
                frequency_hz=freq, magnitude=r.magnitude / 4096.0,
                direction_deg=self._effect_dirs.get(r.effectBlockIndex, 0.0),
            )
        elif report_id == HID_REPORT_ID_EFFECT_OPERATION:
            r = FFBReport_EffectOperation.from_buffer_copy(data)
            if r.operation == OP_STOP:
                self._agg.stop(r.effectBlockIndex)
            else:  # OP_START / OP_START_SOLO / OP_START_OVERRIDE
                self._agg.start(r.effectBlockIndex)
        elif report_id == HID_REPORT_ID_BLOCK_FREE:
            eid = data[1] if len(data) > 1 else 0
            self._agg.remove(eid)
            self._effect_types.pop(eid, None)
            self._effect_dirs.pop(eid, None)
        # DEVICE_CONTROL / DEVICE_GAIN / DEADZONE etc. are not consumed here.

    # --- downlink tick -----------------------------------------------------
    def _assignments(self) -> Dict[str, tuple]:
        """Map axis-key -> (function_id, AxisScale) from role + discovered config."""
        out = {}
        for key, fn in self._assignments_fn.items():
            out[key] = (fn, self._axis_scale(fn))
        return out

    def _axis_scale(self, function_id: int) -> AxisScale:
        rng = self._pos_range(function_id)
        mm_half = (rng[1] - rng[0]) / 2.0 if rng else 1.0
        return AxisScale(mm_half_range=mm_half or 1.0, max_force_n=self._max_force_n)

    def _pos_range(self, function_id: int) -> Optional[tuple]:
        return self._link.topo.pos_range(function_id)

    def _tick(self):
        for function_id, action in self._agg.aggregate(self._assignments()).items():
            self._link.send_flight_ffb(function_id, action)

    # --- input readback ----------------------------------------------------
    def get_input(self) -> FFBReport_Input:
        rep = FFBReport_Input()
        rep.reportId = 1
        x_fn = self._assignments_fn.get("x")
        y_fn = self._assignments_fn.get("y")
        if x_fn is not None:
            rep.X = self._norm_pos(x_fn)
            rep.RawX = rep.X
            rep.ForceX = self._norm_force(x_fn)
            rep.CP_offsetX = self._trim_cp(x_fn)
        if y_fn is not None:
            rep.Y = self._norm_pos(y_fn)
            rep.RawY = rep.Y
            rep.ForceY = self._norm_force(y_fn)
            rep.CP_offsetY = self._trim_cp(y_fn)
        return rep

    def _norm_pos(self, function_id: int) -> int:
        """Function position -> int16 [-4096..4096]."""
        pos = self._link.state.function_position(function_id)
        rng = self._pos_range(function_id)
        if rng and rng[1] != rng[0]:
            center = (rng[0] + rng[1]) / 2.0
            norm = 2.0 * (pos - center) / (rng[1] - rng[0])
        else:
            norm = pos  # assume already normalized until config discovered
        return int(round(max(-1.0, min(1.0, norm)) * 4096))

    def _norm_force(self, function_id: int) -> int:
        f = self._link.state.function_force(function_id) / (self._max_force_n or 1.0)
        return int(round(max(-1.0, min(1.0, f)) * 4096))

    def _trim_cp(self, function_id: int) -> int:
        rng = self._pos_range(function_id)
        act = self._agg.aggregate(self._assignments()).get(function_id)
        if not act or not rng:
            return 0
        mm_half = (rng[1] - rng[0]) / 2.0
        norm = (act.trim_offset / mm_half) if mm_half else 0.0
        return int(round(max(-1.0, min(1.0, norm)) * 4096))

    # --- optional / no-op device methods ----------------------------------
    def supports_axis_override(self) -> bool:
        return False

    def _on_device_info(self, msg: pb.Message):
        di = msg.device_info
        self._fw_version = di.fw_version or self._fw_version
        if di.board:
            self.info.product_string = di.board
        if di.device_uid:
            self.info.serial_number = di.device_uid


def open_diy_device(device_type="joystick", host="127.0.0.1", port=45111) -> DiyFfbDevice:
    """Factory for the main.py backend branch: build, open, and return the
    device to assign to HapticEffect.device."""
    return DiyFfbDevice(device_type=device_type, host=host, port=port).open()

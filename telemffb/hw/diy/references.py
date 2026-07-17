"""Host-side per-function reference config for the DIY backend (plan 27 §2.1).

TelemFFB emits every effect as a 0..1 ratio to a baseline; `DiyFfbDevice` scales
those ratios into the ABSOLUTE physical units the firmware expects (N / mm /
N·s/mm) before sending `FlightFfbAction` (plan 26 §6.0). Those scaling
references live **host-side** (the firmware already runs on absolute units) and
are tuned per rig here — a human-editable JSON file that `DiyFfbDevice` loads at
open and re-reads live on change.

`base_damping_ns_per_mm` is a **safety floor** (always applied) so a direct-drive
axis is never left undamped — keep it > 0.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from typing import Dict

from .aggregator import AxisReferences

log = logging.getLogger("diy_references")

# Conservative, over-damped placeholders — MUST be tuned per rig. Over-damped is
# safe; under-damped risks oscillation on a direct-drive axis.
DEFAULTS = AxisReferences(
    spring_n_per_mm=2.0,
    damper_ns_per_mm=1.0,
    base_damping_ns_per_mm=0.5,   # safety floor — keep > 0
    friction_n=5.0,
    load_n=60.0,
)

# Functions the DIY backend can drive (JSON keys; trim range comes from
# discovery, so it is intentionally NOT persisted here).
FUNCTION_NAMES = [
    "FLIGHT_STICK_ROLL",
    "FLIGHT_STICK_PITCH",
    "FLIGHT_STICK_COLLECTIVE",
    "FLIGHT_PEDALS",
]
PERSISTED_FIELDS = [
    "spring_n_per_mm",
    "damper_ns_per_mm",
    "base_damping_ns_per_mm",
    "friction_n",
    "load_n",
]
_README = ("Per-function DIY FFB references (absolute units). TelemFFB ratios "
           "are scaled by these before sending. base_damping_ns_per_mm is a "
           "safety floor — keep > 0. Edit and save; TelemFFB re-reads live.")


def _name_to_id(name: str) -> int:
    from . import diy_ffb_protocol_pb2 as pb
    return pb.FunctionID.Value("FUNCTION_ID_" + name)


def _to_dict(refs: AxisReferences) -> dict:
    d = dataclasses.asdict(refs)
    return {k: d[k] for k in PERSISTED_FIELDS}


def default_path() -> str:
    """Default references file location (platform config dir)."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return os.path.join(base, "VPForce-TelemFFB", "diy_references.json")
    return os.path.join(os.path.expanduser("~"), ".diy_ffb_references.json")


def _template() -> dict:
    out = {"_README": _README}
    for name in FUNCTION_NAMES:
        out[name] = _to_dict(DEFAULTS)
    return out


def save(path: str, refs_by_id: Dict[int, AxisReferences]) -> None:
    out = {"_README": _README}
    for fid, refs in refs_by_id.items():
        from . import diy_ffb_protocol_pb2 as pb
        name = pb.FunctionID.Name(fid).removeprefix("FUNCTION_ID_")
        out[name] = _to_dict(refs)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


def write_template(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_template(), f, indent=2)


def load(path: str, create_missing: bool = True) -> Dict[int, AxisReferences]:
    """Return {function_id: AxisReferences}, file values merged over DEFAULTS.

    Writes a template on first use (missing file) so the user has something safe
    and editable. Malformed files fall back to defaults (logged), never crash.
    """
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:  # noqa: BLE001
            log.exception("bad references file %s — using defaults", path)
            data = {}
    elif create_missing:
        try:
            write_template(path)
            log.info("wrote DIY references template: %s", path)
        except Exception:  # noqa: BLE001
            log.exception("could not write references template %s", path)

    result: Dict[int, AxisReferences] = {}
    for name in FUNCTION_NAMES:
        entry = data.get(name, {}) if isinstance(data.get(name), dict) else {}
        vals = {k: entry[k] for k in PERSISTED_FIELDS
                if isinstance(entry.get(k), (int, float))}
        result[_name_to_id(name)] = dataclasses.replace(DEFAULTS, **vals)
    return result

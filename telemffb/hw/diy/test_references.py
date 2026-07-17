"""Offline tests for the host-side references config.
Run: python -m telemffb.hw.diy.test_references
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from . import diy_ffb_protocol_pb2 as pb  # noqa: E402
from . import references as R  # noqa: E402

ROLL = pb.FUNCTION_ID_FLIGHT_STICK_ROLL
PEDALS = pb.FUNCTION_ID_FLIGHT_PEDALS


def _tmp():
    d = tempfile.mkdtemp()
    return os.path.join(d, "sub", "diy_references.json")  # sub/ tests dir creation


def test_missing_file_writes_template_and_returns_defaults():
    path = _tmp()
    refs = R.load(path)
    assert os.path.exists(path), "template should be written on first load"
    assert refs[ROLL] == R.DEFAULTS
    # template is valid JSON with all functions + safety floor > 0
    data = json.load(open(path, encoding="utf-8"))
    for name in R.FUNCTION_NAMES:
        assert data[name]["base_damping_ns_per_mm"] > 0


def test_file_values_merge_over_defaults():
    path = _tmp()
    R.write_template(path)
    data = json.load(open(path, encoding="utf-8"))
    data["FLIGHT_STICK_ROLL"]["spring_n_per_mm"] = 12.5
    data["FLIGHT_STICK_ROLL"]["base_damping_ns_per_mm"] = 1.5
    json.dump(data, open(path, "w", encoding="utf-8"))
    refs = R.load(path)
    assert refs[ROLL].spring_n_per_mm == 12.5
    assert refs[ROLL].base_damping_ns_per_mm == 1.5
    # untouched fields fall back to defaults
    assert refs[ROLL].friction_n == R.DEFAULTS.friction_n
    # other functions untouched
    assert refs[PEDALS] == R.DEFAULTS


def test_save_roundtrip():
    path = _tmp()
    import dataclasses
    edited = {ROLL: dataclasses.replace(R.DEFAULTS, damper_ns_per_mm=3.0)}
    R.save(path, edited)
    back = R.load(path)
    assert back[ROLL].damper_ns_per_mm == 3.0


def test_malformed_file_falls_back_to_defaults():
    path = _tmp()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write("{ not valid json ]")
    refs = R.load(path)
    assert refs[ROLL] == R.DEFAULTS   # no crash, safe defaults


def test_trim_range_not_persisted():
    path = _tmp()
    R.write_template(path)
    data = json.load(open(path, encoding="utf-8"))
    assert "trim_mm_half_range" not in data["FLIGHT_STICK_ROLL"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"FAIL  {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)

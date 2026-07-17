"""Offline test for the grip-button decode. Run: python -m telemffb.hw.diy.test_buttons

The HID open/poll needs hardware; the decode is pure and tested here, mirroring
the FFBReport_Input button split DiyFfbDevice uses.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from buttons import BUTTON_BYTES, REPORT_ID, decode_buttons  # noqa: E402


def _report(button_bits: int) -> bytes:
    return bytes([REPORT_ID]) + button_bits.to_bytes(BUTTON_BYTES, "little") + b"\x00\x08" * 6


def test_no_buttons():
    assert decode_buttons(_report(0)) == 0


def test_button_1_and_48():
    bits = (1 << 0) | (1 << 47)
    assert decode_buttons(_report(bits)) == bits


def test_grip_range_24_47():
    # firmware puts grip buttons at indices 24..47
    bits = (1 << 24) | (1 << 30) | (1 << 47)
    assert decode_buttons(_report(bits)) == bits


def test_wrong_report_id_ignored():
    r = bytearray(_report(0xFF))
    r[0] = 1  # not the gamepad report
    assert decode_buttons(bytes(r)) == 0


def test_short_report_ignored():
    assert decode_buttons(bytes([REPORT_ID, 0x01])) == 0


def test_split_matches_ffbreport_fields():
    # bit0 (button 1) and bit40 (button 41) -> Button0_31 and Button32_47
    b = decode_buttons(_report((1 << 0) | (1 << 40)))
    assert (b & 0xFFFFFFFF) == 0x1
    assert ((b >> 32) & 0xFFFF) == (1 << 8)  # bit 40 -> bit 8 of the 32..47 word


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

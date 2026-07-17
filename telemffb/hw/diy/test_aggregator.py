"""Tests for the effect aggregator. Run: python -m telemffb.hw.diy.test_aggregator

Covers the LVAR-API critical path: ratios scaled to absolute physical units via
per-function references, the always-on base-damping safety floor, spring-center
(trim), master gain, and the cyclic X->roll / Y->pitch fan-out.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from . import diy_ffb_protocol_pb2 as pb  # noqa: E402
from .aggregator import (  # noqa: E402
    EFFECT_CONSTANT,
    EFFECT_DAMPER,
    EFFECT_FRICTION,
    EFFECT_SPRING,
    X_BLOCK,
    Y_BLOCK,
    AxisReferences,
    EffectAggregator,
)

ROLL = pb.FUNCTION_ID_FLIGHT_STICK_ROLL
PITCH = pb.FUNCTION_ID_FLIGHT_STICK_PITCH
PEDALS = pb.FUNCTION_ID_FLIGHT_PEDALS

# references chosen so the scaled arithmetic is easy to eyeball
REF = AxisReferences(spring_n_per_mm=4.0, damper_ns_per_mm=2.0,
                     base_damping_ns_per_mm=0.5, friction_n=10.0,
                     load_n=50.0, trim_mm_half_range=10.0)


def test_ratios_scaled_to_absolute_units():
    ag = EffectAggregator()
    ag.set_condition(1, EFFECT_SPRING, X_BLOCK, coef=0.5, cp_offset=0.2)
    ag.set_condition(2, EFFECT_DAMPER, X_BLOCK, coef=0.3, cp_offset=0.0)
    ag.set_condition(3, EFFECT_FRICTION, X_BLOCK, coef=0.1, cp_offset=0.0)
    for i in (1, 2, 3):
        ag.start(i)
    act = ag.aggregate({"x": (PEDALS, REF)})[PEDALS]
    assert abs(act.k_spring - 0.5 * 4.0) < 1e-5              # ratio * N/mm
    assert abs(act.k_damper - (0.5 + 0.3 * 2.0)) < 1e-5      # base + ratio * N·s/mm
    assert abs(act.k_friction - 0.1 * 10.0) < 1e-5          # ratio * N
    assert abs(act.trim_offset - 0.2 * 10.0) < 1e-4         # cp_offset * mm_half


def test_base_damping_floor_always_present():
    """The safety floor is applied even with no damper effect and at gain 0 —
    a direct-drive axis is never left undamped."""
    ag = EffectAggregator()
    act = ag.aggregate({"x": (PEDALS, REF)})[PEDALS]
    assert abs(act.k_damper - REF.base_damping_ns_per_mm) < 1e-6
    ag.master_gain = 0.0
    act = ag.aggregate({"x": (PEDALS, REF)})[PEDALS]
    assert abs(act.k_damper - REF.base_damping_ns_per_mm) < 1e-6


def test_stopped_effects_excluded_but_floor_stays():
    ag = EffectAggregator()
    ag.set_condition(1, EFFECT_SPRING, X_BLOCK, coef=0.7, cp_offset=0.0)
    act = ag.aggregate({"x": (PEDALS, REF)})[PEDALS]   # not started
    assert act.k_spring == 0.0
    assert abs(act.k_damper - REF.base_damping_ns_per_mm) < 1e-6
    ag.start(1)
    assert abs(ag.aggregate({"x": (PEDALS, REF)})[PEDALS].k_spring - 0.7 * 4.0) < 1e-5


def test_coefficient_weighted_spring_center():
    ag = EffectAggregator()
    ag.set_condition(1, EFFECT_SPRING, X_BLOCK, coef=0.8, cp_offset=0.4)
    ag.set_condition(2, EFFECT_SPRING, X_BLOCK, coef=0.2, cp_offset=-0.4)
    ag.start(1); ag.start(2)
    act = ag.aggregate({"x": (PEDALS, REF)})[PEDALS]
    # weighted center = (0.4*0.8 + -0.4*0.2)/1.0 = 0.24
    assert abs(act.trim_offset - 0.24 * 10.0) < 1e-4
    assert abs(act.k_spring - 1.0 * 4.0) < 1e-5


def test_master_gain_scales_active_not_floor():
    ag = EffectAggregator()
    ag.set_condition(1, EFFECT_SPRING, X_BLOCK, coef=0.6, cp_offset=0.0)
    ag.set_condition(2, EFFECT_DAMPER, X_BLOCK, coef=0.4, cp_offset=0.0)
    ag.start(1); ag.start(2)
    ag.master_gain = 0.5
    act = ag.aggregate({"x": (PEDALS, REF)})[PEDALS]
    assert abs(act.k_spring - 0.6 * 4.0 * 0.5) < 1e-5
    # base damping is NOT scaled by gain; active damper is
    assert abs(act.k_damper - (0.5 + 0.4 * 2.0 * 0.5)) < 1e-5


def test_cyclic_fanout_x_roll_y_pitch():
    ag = EffectAggregator()
    ag.set_condition(1, EFFECT_SPRING, X_BLOCK, coef=0.5, cp_offset=0.1)   # roll
    ag.set_condition(1, EFFECT_SPRING, Y_BLOCK, coef=0.9, cp_offset=-0.2)  # pitch
    ag.start(1)
    out = ag.aggregate({"x": (ROLL, REF), "y": (PITCH, REF)})
    assert set(out) == {ROLL, PITCH}
    assert abs(out[ROLL].k_spring - 0.5 * 4.0) < 1e-5
    assert abs(out[ROLL].trim_offset - 0.1 * 10.0) < 1e-4
    assert abs(out[PITCH].k_spring - 0.9 * 4.0) < 1e-5
    assert abs(out[PITCH].trim_offset - (-0.2) * 10.0) < 1e-4


def test_constant_force_direction_and_scale():
    ag = EffectAggregator()
    ag.set_constant(1, magnitude=0.8, direction_deg=0.0)   # full +X (roll)
    ag.start(1)
    out = ag.aggregate({"x": (ROLL, REF), "y": (PITCH, REF)})
    assert abs(out[ROLL].load_force - 0.8 * 50.0) < 1e-3    # ratio * load_n
    assert abs(out[PITCH].load_force) < 1e-3
    ag.set_constant(1, magnitude=0.8, direction_deg=90.0)   # full +Y (pitch)
    out = ag.aggregate({"x": (ROLL, REF), "y": (PITCH, REF)})
    assert abs(out[ROLL].load_force) < 1e-3
    assert abs(out[PITCH].load_force - 0.8 * 50.0) < 1e-3


def test_remove_and_clear():
    ag = EffectAggregator()
    ag.set_condition(1, EFFECT_SPRING, X_BLOCK, coef=0.5, cp_offset=0.0)
    ag.start(1)
    ag.remove(1)
    assert ag.aggregate({"x": (PEDALS, REF)})[PEDALS].k_spring == 0.0


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

# DIY FFB × TelemFFB — Hardware Bring-Up

Getting TelemFFB to drive the DIY rig (cyclic + collective + pedals) through the
serial broker, on the sim PC.

**Scope:** this is the **transport/backend** bring-up — prove TelemFFB's ordinary
flight FFB (a spring) reaches the hardware. The **`L:FFB_*` LVAR API** test is a
separate step on top (needs `FFBApiHelicopter` + an aircraft-side stub); do this
first. Rationale/design: DIY-FFB repo `docs/plans/26_telemffb_python_backend.md`.

---

## 0. Prerequisites (sim PC)

- **Rig working under SimHub** — gateway + axis controllers flashed and talking.
- **Rig configured as flight functions in SimHub**: `FLIGHT_STICK_PITCH` +
  `FLIGHT_STICK_ROLL` (cyclic), `FLIGHT_STICK_COLLECTIVE`, `FLIGHT_PEDALS`, each
  with a centering spring and sane pos range. **Note the gateway COM port.**
- **TelemFFB from source**, branch `ck_diy_ffb_bridge`. The DIY backend must run
  via `python main.py` — its imports are **not** in the packaged `.exe`.
- **Python deps** in TelemFFB's interpreter: `pip install pyserial protobuf`.
- **protobuf version:** `diy_ffb_protocol_pb2.py` was generated with protoc 31.1
  (runtime 6.31). If import fails, `pip install "protobuf>=5"` or regenerate with
  `telemffb/hw/diy/regen_proto.sh`.

## 1. Free the COM port

SimHub is **single-open** on the gateway port. Disconnect SimHub from that
gateway (or close SimHub). **SimHub and TelemFFB are mutually exclusive on the
port** — the broker owns it while TelemFFB runs.

---

## Stage A — Transport smoke test (no TelemFFB)

Prove the broker owns the port and the wire works both directions.

```bash
python -m telemffb.hw.diy.broker --port COM7 -v      # your gateway port
# second terminal:
python -m telemffb.hw.diy.monitor --filter axis_state
python -m telemffb.hw.diy.monitor --request-info
```

**Success:** moving each control prints `axis_state axis=N pos=… force=…`;
`gateway_state` shows `axes_present`; `--request-info` yields `device_info`
(fw / board / uid). This is the hard part (port, baud, framing) proven.

**If nothing:** wrong COM port · SimHub still holds it · check the broker `-v`
log for CRC-error counts (framing/baud mismatch — broker uses 3 Mbaud).

Then `Ctrl-C` the manual broker — from Stage B on, the master auto-starts its own.

---

## Stage B — One control (cyclic) + forces

```bash
python main.py --backend diy --broker-serial COM7 -t joystick
```

Single instance (leave `autolaunchPedals/Collective` off). The master
auto-starts the broker on `COM7`; TelemFFB should show the device connected.
Start a sim (X-Plane or MSFS is easiest — TelemFFB fully synthesizes their FFB)
with any aircraft.

**Success:** the cyclic springs/centers on **both** axes; deflecting it changes
force (watch a `monitor` in parallel, or the TelemFFB log).

---

## Stage C — Full rig

```bash
python main.py --backend diy --broker-serial COM7
```

The master brings up cyclic + collective + pedals — autolaunched children
inherit `--backend diy` and connect to the one broker.

**Success:** all four functions respond in-sim; each control instance shows
connected in its window.

---

## Tuning the feel (references) — do this early, it's a safety knob

TelemFFB sends 0..1 ratios; `DiyFfbDevice` scales them to absolute N / mm /
N·s/mm using a **per-function references file** (`references.py`). On first run
it writes a template at `%LOCALAPPDATA%\VPForce-TelemFFB\diy_references.json`
(override with `--references PATH`). Edit it and save — TelemFFB **re-reads it
live** (~1×/s) while driving.

Each function has `spring_n_per_mm`, `damper_ns_per_mm`,
**`base_damping_ns_per_mm`**, `friction_n`, `load_n`. The defaults are
deliberately **conservative / over-damped** — safe but stiff. Tune them down to
taste per axis. **Keep `base_damping_ns_per_mm` > 0** on every axis: it's the
always-on damping floor that stops a direct-drive axis from oscillating when the
active ratio is zero.

## Known rough edges (expected, not failures)

- **Force scaling is uncalibrated** (`k_spring` gain, `max_force_n = 60 N`, trim
  in mm) — feel may be weak/strong. This stage validates *plumbing*, not
  fidelity; tune after.
- **First real run of `ffb_diy.py`** under PyQt6/hidapi — watch the TelemFFB log
  for an import/runtime snag.
- **Discovery is event-driven:** per-axis pos-range normalization and
  fly-through only populate once the gateway has reported each axis present
  (`GatewayState`). Give it a moment after connect.

## Backend limitations (by design, not bugs)

The full `HapticEffect.device` method surface is implemented, so no
`AttributeError` crashes are expected from the device interface. These behaviours
differ from a VPforce device and are expected:

- **Buttons come from the DIY HID gamepads** (`buttons.py`), not the FFB serial
  link. `DiyFfbDevice` opens **every** DIY gamepad interface (VID `0x303b`,
  product `DIY-FFB-*` — gateway and each axis controller) via TelemFFB's hidapi
  and **ORs** their 48 buttons into `get_input()`, so the grip works wherever
  it's wired. Device-button features (force-trim / spring-override, the binding
  dialog) work. Requires the gamepads enumerable and `hidapi.dll` present (both
  true on a normal TelemFFB-from-source setup). If none are found, buttons are
  simply unavailable (logged once) — the FFB path is unaffected.
- **`CP_XY()` never reports "no spring center."** The Rhino signals that with an
  out-of-range center offset; we always report a clamped center. Benign.
- **VPConf profiles don't apply.** Aircraft profiles with a `vpconf` param, or a
  global VPConf default (`enableVPConfGlobalDefault`), target the VPforce
  Configurator — meaningless here. Leave the global VPConf default **off** with
  the DIY backend.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Broker can't open the port | wrong COM · SimHub still connected · port busy |
| `ModuleNotFoundError: protobuf` | `pip install protobuf` in TelemFFB's Python |
| protobuf "duplicate file/descriptor pool" | a second copy of `diy_ffb_protocol_pb2` on `PYTHONPATH` (e.g. an old `tools/diy_ffb_bridge`) — remove it |
| `monitor` shows `axis_state` but no force in-sim | SimHub function config (no centering spring / bad pos range) · sim not feeding telemetry · `-t` doesn't match a configured function |
| Children come up as Rhino and fail | build predates `--backend` child propagation — ensure the branch includes commit `dbb52cb` |

---

## Done → next

Transport bring-up is complete when Stage A shows live `axis_state` both ways and
Stage B/C give real spring forces in a sim. Then move to the **LVAR-API test**:
run `FFBApiHelicopter` as the cyclic instance against an aircraft-side stub that
publishes `L:FFB_API_VERSION=1` + a movable `L:FFB_CYCLIC_*_TRIM`.

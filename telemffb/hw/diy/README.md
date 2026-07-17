# DIY FFB serial bridge (`telemffb.hw.diy`)

Self-contained package that lets TelemFFB drive DIY ESP32 FFB hardware over the
gateway's serial link. Consumed by [`telemffb/hw/ffb_diy.py`](../ffb_diy.py)
(`DiyFfbDevice`). Vendored from the DIY-FFB repo; the protocol source of truth
is that repo's `proto/diy_ffb_protocol.proto` (see rationale in its
`docs/plans/26_telemffb_python_backend.md`, §5.1).

## Pieces

- **`framing.py`** — the wire codec: COBS + CRC16/Modbus + `StreamDecoder`,
  matching the SimHub plugin and ESP32 firmware.
- **`broker.py`** — a protocol-agnostic process that **owns the gateway COM
  port** and multiplexes it across the (separate-process) TelemFFB instances
  (cyclic / collective / pedals), which otherwise can't share the single-open
  port. Downstream it just write-arbitrates each client's already-`function_id`-
  tagged `FFBAction`; upstream it broadcasts decoded `Message`s and each client
  filters the `AxisState` it owns.
- **`client.py`** — `BrokerClient`, `Topology` (event-driven discovery via
  `GatewayState` + `return_function_config`), `AxisStateTracker` (per-function
  position/force), and `DiyFfbLink`.
- **`aggregator.py`** — `EffectAggregator`: collapses TelemFFB effects into
  per-function `FlightFfbAction` (cyclic X→roll / Y→pitch fan-out).
- **`monitor.py`** — decodes/prints the uplink; the first hardware smoke test.
- **`diy_ffb_protocol_pb2.py`** — generated; regenerate with `./regen_proto.sh`.
- **`test_*.py`** — offline tests (no hardware).

## Requirements

Python 3.11+, `pyserial` (broker), `protobuf` (clients/monitor).

## Run

Ensure SimHub is **not** holding the gateway COM port (single-open).

**One-shot (recommended):** the master auto-starts the broker and propagates the
DIY backend to its autolaunched children (cyclic / collective / pedals):

```bash
python main.py --backend diy --broker-serial COM7
```

**Manual broker** (e.g. for the smoke test, or an external broker): omit
`--broker-serial` and run the broker yourself:

```bash
python -m telemffb.hw.diy.broker --port COM7 -v
python -m telemffb.hw.diy.monitor --filter axis_state   # watch decoded uplink
python main.py --backend diy -t joystick                # then the instance(s)
```

Broker TCP address defaults to `127.0.0.1:45111`; override with `--broker HOST:PORT`.

## Test (offline, no hardware)

```bash
python -m telemffb.hw.diy.test_framing
python -m telemffb.hw.diy.test_broker
python -m telemffb.hw.diy.test_client
python -m telemffb.hw.diy.test_aggregator
```

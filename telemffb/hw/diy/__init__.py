"""DIY FFB serial bridge: wire codec, broker, discovery + effect aggregator.

Vendored into TelemFFB as a self-contained package; consumed by
``telemffb.hw.ffb_diy``. Protocol source of truth is the ``.proto`` in the
DIY-FFB repo; regenerate ``diy_ffb_protocol_pb2.py`` with ``regen_proto.sh``.
"""

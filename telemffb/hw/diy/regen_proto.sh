#!/usr/bin/env bash
# Regenerate diy_ffb_protocol_pb2.py from the authoritative .proto, which lives
# in the DIY-FFB repo (shared with the firmware + SimHub plugin). Dev-time only;
# the cross-repo path is not needed at runtime.
#
# Override the proto location with DIY_FFB_PROTO_DIR; otherwise assume the
# DIY-FFB repo sits beside this one under a common parent.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
proto_dir="${DIY_FFB_PROTO_DIR:-$here/../../../../DIY-Sim-Racing-FFB-Pedal/proto}"
if [ ! -f "$proto_dir/diy_ffb_protocol.proto" ]; then
  echo "proto not found in '$proto_dir' — set DIY_FFB_PROTO_DIR" >&2
  exit 1
fi
protoc --python_out="$here" --proto_path="$proto_dir" "$proto_dir/diy_ffb_protocol.proto"
echo "regenerated diy_ffb_protocol_pb2.py from $proto_dir"

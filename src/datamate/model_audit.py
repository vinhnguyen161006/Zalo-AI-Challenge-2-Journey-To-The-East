"""Audit stored safetensors shapes without importing torch or loading tensor data."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any

from datamate.errors import InvalidInput


def audit_checkpoint(root: Path) -> dict[str, Any]:
    files = sorted(root.glob("*.safetensors"))
    if not files:
        raise InvalidInput("No safetensors shards found")
    tensors: dict[str, int] = {}
    file_reports = []
    for path in files:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            prefix = stream.read(8)
            if len(prefix) != 8:
                raise InvalidInput("Invalid safetensors prefix")
            size = struct.unpack("<Q", prefix)[0]
            if size > 100000000 or size > path.stat().st_size - 8:
                raise InvalidInput("Invalid safetensors header size")
            header = json.loads(stream.read(size))
            digest.update(prefix)
            stream.seek(8)
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        intervals: list[tuple[int, int]] = []
        dtype_bytes = {
            "F64": 8,
            "F32": 4,
            "F16": 2,
            "BF16": 2,
            "I64": 8,
            "I32": 4,
            "I16": 2,
            "I8": 1,
            "U8": 1,
            "BOOL": 1,
            "F8_E4M3": 1,
            "F8_E5M2": 1,
        }
        for name, tensor in header.items():
            if name == "__metadata__":
                continue
            if name in tensors:
                raise InvalidInput(f"Duplicate tensor across shards: {name}")
            shape, offsets = tensor.get("shape"), tensor.get("data_offsets")
            if not isinstance(shape, list) or any(type(d) is not int or d < 0 for d in shape):
                raise InvalidInput("Invalid tensor shape")
            if (
                not isinstance(offsets, list)
                or len(offsets) != 2
                or any(type(v) is not int for v in offsets)
            ):
                raise InvalidInput("Invalid tensor offsets")
            lo, hi = offsets
            if lo < 0 or hi < lo or hi > path.stat().st_size - size - 8:
                raise InvalidInput("Tensor offsets exceed shard")
            count = math.prod(shape)
            width = dtype_bytes.get(tensor.get("dtype"))
            if width is None or hi - lo != count * width:
                raise InvalidInput("Unsupported dtype or shape/byte mismatch")
            if any(lo < prior_hi and hi > prior_lo for prior_lo, prior_hi in intervals):
                raise InvalidInput(
                    "Overlapping storage/tied weights require an explicit audit policy"
                )
            intervals.append((lo, hi))
            tensors[name] = count
        file_reports.append({"file": path.name, "sha256": digest.hexdigest()})
    index = root / "model.safetensors.index.json"
    if index.exists():
        mapping = json.loads(index.read_text(encoding="utf-8"))["weight_map"]
        if set(mapping) != set(tensors) or not set(mapping.values()) <= {p.name for p in files}:
            raise InvalidInput("Missing shards or index tensor mismatch")
    return {
        "stored_elements": sum(tensors.values()),
        "tensor_count": len(tensors),
        "files": file_reports,
        "eligibility": "UNVERIFIED",
        "method": "safetensors header shapes; hashes cover complete shard bytes",
        "caveats": [
            "Stored elements are not a legal parameter count for packed quantized weights.",
            "Tied/auxiliary/adapter treatment and release eligibility require official policy.",
        ],
    }

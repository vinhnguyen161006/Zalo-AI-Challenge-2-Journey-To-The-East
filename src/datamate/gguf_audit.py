"""Read GGUF tensor shapes without loading torch, CUDA or model weights into RAM."""

from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Any


def audit_gguf(path: Path) -> dict[str, Any]:
    sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
    file_size = path.stat().st_size
    with path.open("rb") as stream:

        def read(count: int) -> bytes:
            value = stream.read(count)
            if len(value) != count:
                raise ValueError("Truncated GGUF header")
            return value

        def number(fmt: str) -> int:
            return int(struct.unpack("<" + fmt, read(struct.calcsize("<" + fmt)))[0])

        def string(keep: bool = True) -> str:
            length = number("Q")
            if length > file_size or stream.tell() + length > file_size:
                raise ValueError("Invalid GGUF string length")
            if keep:
                if length > 2000000:
                    raise ValueError("GGUF string exceeds audit limit")
                return read(length).decode("utf-8")
            stream.seek(length, 1)
            return ""

        def value(kind: int, keep: bool = False) -> Any:
            if kind == 8:
                return string(keep)
            if kind == 9:
                subtype, count = number("I"), number("Q")
                if subtype == 9 or count > 2000000:
                    raise ValueError("Unsupported nested or oversized GGUF array")
                for _ in range(count):
                    value(subtype)
                return None
            if kind not in sizes:
                raise ValueError("Unknown GGUF metadata type")
            raw = read(sizes[kind])
            return int.from_bytes(raw, "little") if keep else None

        if read(4) != b"GGUF" or number("I") not in {2, 3}:
            raise ValueError("Expected GGUF version 2 or 3")
        tensor_count, metadata_count = number("Q"), number("Q")
        if tensor_count > 1000000 or metadata_count > 1000000:
            raise ValueError("GGUF header count exceeds audit limit")
        metadata = {}
        for _ in range(metadata_count):
            key, kind = string(), number("I")
            keep = key in {"general.architecture", "general.name", "general.alignment"}
            item = value(kind, keep)
            if keep:
                metadata[key] = item
        tensors: list[dict[str, Any]] = []
        names: set[str] = set()
        parameters = 0
        for _ in range(tensor_count):
            name, dimensions = string(), number("I")
            if name in names or not 1 <= dimensions <= 8:
                raise ValueError("Duplicate tensor name or invalid GGUF dimensions")
            names.add(name)
            shape = [number("Q") for _ in range(dimensions)]
            kind, offset = number("I"), number("Q")
            if not all(0 < n <= 1000000000 for n in shape) or offset >= file_size:
                raise ValueError("Invalid GGUF tensor shape or offset")
            count = math.prod(shape)
            parameters += count
            tensors.append(
                {
                    "name": name,
                    "shape": shape,
                    "elements": count,
                    "ggml_type": kind,
                    "offset": offset,
                }
            )
        alignment = metadata.get("general.alignment", 32)
        if not isinstance(alignment, int) or alignment < 1 or alignment > 1048576:
            raise ValueError("Invalid GGUF alignment")
        data_start = (stream.tell() + alignment - 1) // alignment * alignment
        if any(data_start + tensor["offset"] >= file_size for tensor in tensors):
            raise ValueError("GGUF tensor offset outside file")
    return {
        "format": "GGUF",
        "path": str(path),
        "file_bytes": file_size,
        "metadata": metadata,
        "tensor_count": tensor_count,
        "stored_tensor_elements": parameters,
        "tensors": tensors,
        "official_eligibility_verified": False,
    }

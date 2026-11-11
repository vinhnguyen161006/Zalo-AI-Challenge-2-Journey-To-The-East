import struct
from pathlib import Path

import pytest

from datamate.gguf_audit import audit_gguf


def test_counts_logical_shapes_without_loading_weights(tmp_path: Path) -> None:
    def text(value: bytes) -> bytes:
        return struct.pack("<Q", len(value)) + value

    header = b"GGUF" + struct.pack("<IQQ", 3, 1, 1)
    header += text(b"general.architecture") + struct.pack("<I", 8) + text(b"synthetic")
    header += text(b"weight") + struct.pack("<IQQIQ", 2, 2, 3, 0, 0)
    header += b"\0" * (-len(header) % 32)
    path = tmp_path / "tiny.gguf"
    path.write_bytes(header + b"\0" * 24)
    report = audit_gguf(path)
    assert report["stored_tensor_elements"] == 6
    assert report["tensor_count"] == 1
    assert report["official_eligibility_verified"] is False
    path.write_bytes(header[:-8])
    with pytest.raises(ValueError):
        audit_gguf(path)

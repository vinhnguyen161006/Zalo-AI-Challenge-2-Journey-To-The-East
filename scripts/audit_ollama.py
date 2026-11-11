"""Audit tensor elements of only the explicitly selected local GGUF checkpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from datamate.gguf_audit import audit_gguf


def audit(source: Path, models: list[str]) -> dict[str, Any]:
    counted: dict[str, dict[str, Any]] = {}
    checkpoints = []
    for model in models:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+", model):
            raise ValueError("Expected library model name:tag")
        name, tag = model.split(":")
        manifest = source / "manifests" / "registry.ollama.ai" / "library" / name / tag
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        blobs = [metadata["config"], *metadata["layers"]]
        records = []
        for blob in blobs:
            digest = blob["digest"]
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                raise ValueError("Invalid blob digest")
            path = source / "blobs" / digest.replace(":", "-")
            if path.stat().st_size != blob["size"]:
                raise ValueError("Blob size mismatch")
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != digest.split(":")[1]:
                raise ValueError("Blob hash mismatch")
            with path.open("rb") as stream:
                gguf = stream.read(4) == b"GGUF"
            media = blob.get("mediaType", "")
            if gguf and digest not in counted:
                report = audit_gguf(path)
                report.pop("tensors")
                counted[digest] = report
            elif not gguf and any(token in media for token in (".model", ".projector", ".adapter")):
                raise ValueError(
                    "Selected tensor blob is not supported GGUF; cannot certify its tensor count"
                )
            records.append(
                {"digest": digest, "media_type": media, "bytes": blob["size"], "gguf": gguf}
            )
        if not any(r["gguf"] for r in records):
            raise ValueError("Checkpoint has no audited weight tensors")
        checkpoints.append(
            {
                "model": model,
                "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "blobs": records,
            }
        )
    total = sum(report["stored_tensor_elements"] for report in counted.values())
    return {
        "checkpoints": checkpoints,
        "distinct_tensor_blobs": counted,
        "total_stored_tensor_elements": total,
        "below_15_billion_stored_elements": total <= 15000000000,
        "counting_policy": "GGUF logical tensor shapes, shared content hash counted once; not original-model or organizer accounting certification",
        "official_eligibility_verified": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.home() / ".ollama" / "models")
    parser.add_argument("--model", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Use a new output path")
    result = audit(args.source, args.model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"checkpoints", "distinct_tensor_blobs"}}
        )
    )

"""Export only selected installed models and verified blobs for an offline image."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def export(source: Path, destination: Path, model: str) -> dict[str, object]:
    return export_many(source, destination, [model])[0]


def export_many(
    source: Path, destination: Path, models: list[str], *, hardlink: bool = False
) -> list[dict[str, object]]:
    if (
        not models
        or len(models) != len(set(models))
        or any(not re.fullmatch(r"[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+", model) for model in models)
    ):
        raise ValueError("Expected distinct library model name:tag values")
    source, destination = source.resolve(), destination.resolve()
    if destination == source or destination.is_relative_to(source):
        raise ValueError("Export destination must be outside the source store")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Use a new empty destination to avoid packaging extra models")
    manifests, records, reports = [], {}, []
    for model in models:
        name, tag = model.split(":")
        relative = Path("manifests") / "registry.ollama.ai" / "library" / name / tag
        manifest = source / relative
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        blobs = [metadata["config"], *metadata["layers"]]
        for blob in blobs:
            sha = blob["digest"]
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", sha):
                raise ValueError("Invalid blob digest")
            path = source / "blobs" / sha.replace(":", "-")
            if path.stat().st_size != blob["size"] or digest(path) != sha.split(":")[1]:
                raise ValueError(f"Blob integrity failed: {sha}")
            records[sha] = path
        manifests.append((manifest, relative))
        reports.append(
            {
                "model": model,
                "manifest_sha256": digest(manifest),
                "blobs": [b["digest"] for b in blobs],
            }
        )
    (destination / "blobs").mkdir(parents=True, exist_ok=True)
    for path in records.values():
        target = destination / "blobs" / path.name
        if hardlink:
            target.hardlink_to(path)
        else:
            shutil.copyfile(path, target)
    for manifest, relative in manifests:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(manifest, target)
    (destination / "export.json").write_text(
        json.dumps({"models": reports, "hardlinked": hardlink}, indent=2), encoding="utf-8"
    )
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.home() / ".ollama" / "models")
    parser.add_argument("--destination", type=Path, default=Path("models/ollama-export"))
    parser.add_argument("--model", action="append")
    parser.add_argument(
        "--hardlink",
        action="store_true",
        help="Use same-volume hardlinks for immutable blobs to avoid duplicate disk usage",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            export_many(
                args.source,
                args.destination,
                args.model or ["qwen2.5-coder:3b-instruct"],
                hardlink=args.hardlink,
            )
        )
    )

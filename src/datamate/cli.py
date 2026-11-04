"""Runnable project entry points. No command downloads models or trains implicitly."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from datamate.adapters.official_kit import TrainKitAdapter
from datamate.adapters.submission_writer import json_line, read_predictions, write_predictions
from datamate.config import load_config
from datamate.data.ingest import ingest_business, sha256_file
from datamate.errors import DatamateError
from datamate.eval.scorer import diagnostic_score
from datamate.fixtures import fixture_smoke
from datamate.model_audit import audit_checkpoint
from datamate.observability import configure_logging
from datamate.pipeline import Pipeline

app = typer.Typer(no_args_is_help=True, help="AEGDA: offline DataMate development harness.")
ConfigOption = Annotated[Path, typer.Option("--config", "-c")]


@app.callback()
def initialize() -> None:
    configure_logging()


def emit(value: Any) -> None:
    typer.echo(json_line(value))


def fail(exc: Exception) -> None:
    emit({"status": "ERROR", "error_type": type(exc).__name__, "message": str(exc)})
    raise typer.Exit(1)


@app.command()
def preflight(config: ConfigOption = Path("configs/dev.yaml"), mode: str | None = None) -> None:
    """Check input and official-mode gates without loading models."""
    try:
        cfg = load_config(config, mode)
        result = cfg.preflight()
        questions = list(TrainKitAdapter(cfg.data_root).iter_questions(cfg.questions))
        result.update(
            questions=len(questions),
            businesses=len({q.business_id for q in questions}),
            model_backend=cfg.models.backend,
        )
        emit(result)
    except (DatamateError, ValueError, OSError) as exc:
        fail(exc)


@app.command("inspect")
def inspect_business(
    config: ConfigOption = Path("configs/dev.yaml"),
    business_id: str = "biz_demo",
    all_businesses: bool = False,
) -> None:
    """Build local snapshots/catalogs. Never read train answers or mutate source."""
    try:
        cfg = load_config(config)
        cfg.preflight()
        ids = (
            sorted(p.name for p in cfg.data_root.iterdir() if p.is_dir())
            if all_businesses
            else [business_id]
        )
        reports, failures = [], []
        for name in ids:
            try:
                catalog = ingest_business(
                    cfg.data_root, name, cfg.output_root / "cache", cfg.limits
                )
                reports.append(
                    {
                        "business_id": name,
                        "tables": len(catalog.tables),
                        "rows": sum(t.rows for t in catalog.tables),
                        "warnings": catalog.warnings,
                        "catalog": str(Path(catalog.database_path).parent / "catalog.json"),
                    }
                )
                typer.echo(json_line({"event": "business_loaded", "business_id": name}), err=True)
            except (DatamateError, ValueError, OSError) as exc:
                failures.append(
                    {"business_id": name, "error_type": type(exc).__name__, "message": str(exc)}
                )
        report = {"loaded": reports, "failed": failures}
        cfg.output_root.mkdir(parents=True, exist_ok=True)
        (cfg.output_root / "ingestion_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        emit(report)
        if failures:
            raise typer.Exit(1)
    except (DatamateError, ValueError, OSError) as exc:
        fail(exc)


@app.command("test-fixtures")
def test_fixtures(config: ConfigOption = Path("configs/default.yaml")) -> None:
    """Run deterministic synthetic SQL and independent DSL on CPU."""
    try:
        cfg = load_config(config)
        cfg.preflight()
        emit(fixture_smoke(cfg))
    except (DatamateError, ValueError, OSError, AssertionError) as exc:
        fail(exc)


def _predict(config: Path, questions: Path | None, output: Path, resume: bool) -> dict[str, Any]:
    cfg = load_config(config)
    if questions:
        cfg.questions = questions.resolve()
    cfg.preflight()
    records = list(TrainKitAdapter(cfg.data_root).iter_questions(cfg.questions))
    fingerprints = {
        p.relative_to(cfg.data_root).as_posix(): sha256_file(p)
        for p in sorted(cfg.data_root.rglob("*"))
        if p.is_file()
    }
    identity = [
        cfg.model_dump(mode="json"),
        sha256_file(cfg.questions),
        sha256_file(cfg.rulebook),
        sha256_file(cfg.calendar),
        fingerprints,
        "pipeline-0.1.0",
    ]
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    pipeline = Pipeline(cfg)
    count = write_predictions(output, records, pipeline.solve, fingerprint, resume=resume)
    statuses = Counter(r["type"] for r in read_predictions(output))
    return {
        "output": str(output),
        "records": count,
        "types": dict(statuses),
        "official_ready": False,
        "model_backend": cfg.models.backend,
    }


@app.command()
def predict(
    config: ConfigOption = Path("configs/dev.yaml"),
    questions: Path | None = None,
    output: Path = Path("outputs/dev_predictions.jsonl"),
    resume: bool = False,
) -> None:
    """Write one typed record per question; disabled models yield explicit abstentions."""
    try:
        emit(_predict(config, questions, output, resume))
    except (DatamateError, ValueError, OSError) as exc:
        fail(exc)


@app.command()
def validate(
    predictions: Annotated[Path, typer.Option()],
    config: ConfigOption = Path("configs/dev.yaml"),
    questions: Path | None = None,
) -> None:
    """Validate local train/stub schema, counts and order; not the official validator."""
    try:
        cfg = load_config(config)
        cfg.preflight()
        source = questions or cfg.questions
        ids = [q.id for q in TrainKitAdapter(cfg.data_root).iter_questions(source)]
        records = read_predictions(predictions, ids)
        emit(
            {
                "status": "LOCAL_FORMAT_PASS",
                "records": len(records),
                "official_validator": "NOT_AVAILABLE",
            }
        )
    except (DatamateError, ValueError, OSError) as exc:
        fail(exc)


@app.command()
def evaluate(
    labels: Annotated[Path, typer.Option()],
    config: ConfigOption = Path("configs/dev.yaml"),
    questions: Path | None = None,
    output: Path = Path("outputs/eval_predictions.jsonl"),
) -> None:
    """Run dev prediction and an explicitly non-official exact-match diagnostic."""
    try:
        _predict(config, questions, output, False)
        emit(diagnostic_score(output, labels))
    except (DatamateError, ValueError, OSError) as exc:
        fail(exc)


@app.command("audit-models")
def audit_models(
    checkpoint: Annotated[Path | None, typer.Option()] = None,
    config: ConfigOption = Path("configs/models.yaml"),
) -> None:
    """Count stored tensor elements and hash shards without torch/CUDA."""
    try:
        if checkpoint is not None:
            emit(audit_checkpoint(checkpoint))
            return
        manifest = yaml.safe_load(config.read_text(encoding="utf-8"))
        reports = []
        for item in manifest["checkpoints"]:
            path = (config.resolve().parent / item["path"]).resolve()
            try:
                reports.append(
                    {"id": item["id"], "revision": item["revision"], **audit_checkpoint(path)}
                )
            except DatamateError as exc:
                reports.append(
                    {"id": item["id"], "status": "NOT_AUDITED", "error_type": type(exc).__name__}
                )
        emit(
            {
                "checkpoints": reports,
                "parameter_cap": manifest.get("parameter_cap"),
                "eligibility": "UNVERIFIED",
            }
        )
        if any(r.get("status") == "NOT_AUDITED" for r in reports):
            raise typer.Exit(1)
    except (DatamateError, ValueError, OSError) as exc:
        fail(exc)

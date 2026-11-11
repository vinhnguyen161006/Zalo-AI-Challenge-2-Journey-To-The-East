"""Runnable project entry points. No command downloads models or trains implicitly."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from itertools import islice
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from datamate.adapters.official_kit import TrainKitAdapter
from datamate.adapters.submission_writer import json_line, read_predictions, write_predictions
from datamate.config import Config, load_config
from datamate.data.ingest import ingest_business, sha256_file
from datamate.decision.calibration import (
    fit_calibration,
    inference_settings,
    runtime_signature,
    save_calibration,
)
from datamate.errors import DatamateError, InvalidInput
from datamate.eval.scorer import diagnostic_score
from datamate.fixtures import fixture_smoke
from datamate.llm.client import OllamaModelClient
from datamate.model_audit import audit_checkpoint
from datamate.observability import configure_logging
from datamate.pipeline import Pipeline
from datamate.types import InternalDecision, QuestionRecord

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
                    cfg.data_root,
                    name,
                    cfg.output_root / "cache",
                    cfg.limits,
                    rulebook=cfg.rulebook,
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


def _predict(
    config: Path | Config,
    questions: Path | None,
    output: Path,
    resume: bool,
    limit: int | None = None,
) -> dict[str, Any]:
    cfg = load_config(config) if isinstance(config, Path) else config
    if questions:
        cfg.questions = questions.resolve()
    cfg.preflight()
    source = TrainKitAdapter(cfg.data_root).iter_questions(cfg.questions)
    records = list(islice(source, limit)) if limit is not None else list(source)
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
        {
            p.relative_to(Path(__file__).parent).as_posix(): sha256_file(p)
            for p in sorted(Path(__file__).parent.rglob("*"))
            if p.suffix in {".py", ".md"}
        },
        [q.id for q in records],
    ]
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    if cfg.calibration_artifact:
        fingerprint = hashlib.sha256(
            (fingerprint + sha256_file(cfg.calibration_artifact)).encode()
        ).hexdigest()
    trace_directory = output.parent / (
        "logs" if output.name == "submission.jsonl" else output.stem + ".logs"
    )
    pipeline = Pipeline(cfg, trace_directory=trace_directory)
    if cfg.models.backend == "ollama":
        for client, revision in (
            (pipeline.planner, cfg.models.planner_revision),
            (pipeline.reasoner, cfg.models.reasoner_revision),
        ):
            if isinstance(client, OllamaModelClient):
                client.verify_installed(revision)

    def solve_frozen(question: QuestionRecord) -> InternalDecision:
        if runtime_signature(inference_settings(cfg)) != pipeline.runtime_signature:
            raise InvalidInput(
                "Runtime changed during inference; use a new output after code changes"
            )
        result = pipeline.solve(question)
        if runtime_signature(inference_settings(cfg)) != pipeline.runtime_signature:
            raise InvalidInput("Runtime changed during this question; its answer was not published")
        return result

    count = write_predictions(
        output, records, solve_frozen, fingerprint, resume=resume, publish_progress=True
    )
    statuses = Counter(r["type"] for r in read_predictions(output))
    return {
        "output": str(output),
        "records": count,
        "types": dict(statuses),
        "official_ready": False,
        "model_backend": cfg.models.backend,
        "decision_policy": cfg.decision_policy,
        "logs": str(pipeline.trace_directory),
    }


@app.command()
def predict(
    config: ConfigOption = Path("configs/dev.yaml"),
    questions: Path | None = None,
    output: Path = Path("outputs/dev_predictions.jsonl"),
    resume: bool = False,
    limit: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    """Write one typed record per question; disabled models yield explicit abstentions."""
    try:
        emit(_predict(config, questions, output, resume, limit))
    except (DatamateError, ValueError, OSError) as exc:
        fail(exc)


@app.command()
def submit(
    split: Annotated[Path, typer.Option()] = Path("/data"),
    result: Annotated[Path, typer.Option()] = Path("/result"),
    config: ConfigOption = Path("configs/ollama.yaml"),
    resume: bool = False,
    limit: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    """Run a mounted split with its own rules/calendar and write submission plus logs."""
    try:
        cfg = load_config(config)
        split, result = split.resolve(), result.resolve()
        if result == split or result.is_relative_to(split):
            raise ValueError("Result directory must be outside the read-only input split")
        cfg.data_root = split / "data"
        cfg.questions = split / "questions.jsonl"
        cfg.rulebook = split / "rulebook.md"
        cfg.calendar = split / "calendar.csv"
        cfg.output_root = result
        emit(_predict(cfg, None, result / "submission.jsonl", resume, limit))
    except (DatamateError, ValueError, OSError) as exc:
        fail(exc)


@app.command("check-models")
def check_models(config: ConfigOption = Path("configs/ollama.yaml")) -> None:
    """Exercise structured generation against configured local checkpoints."""
    try:
        cfg = load_config(config)
        cfg.preflight()
        pipeline = Pipeline(cfg)
        seen: set[tuple[str, str]] = set()
        reports = []
        for role, client in (("planner", pipeline.planner), ("reasoner", pipeline.reasoner)):
            if client is None:
                raise ValueError("Model backend is disabled")
            identity = (str(getattr(client, "endpoint", "")), str(getattr(client, "model", "")))
            if identity in seen:
                continue
            seen.add(identity)
            response = client.generate_structured(
                [{"role": "user", "content": 'Return exactly {"ready": true}.'}],
                {
                    "type": "object",
                    "properties": {"ready": {"type": "boolean", "const": True}},
                    "required": ["ready"],
                    "additionalProperties": False,
                },
                timeout=min(120, cfg.limits.max_wallclock_seconds),
            )
            if response != {"ready": True}:
                raise ValueError("Model failed structured generation smoke")
            reports.append({"role": role, "model": identity[1], "structured_generation": "PASS"})
        emit({"models": reports, "eligibility_verified": False})
    except (DatamateError, ValueError, OSError) as exc:
        fail(exc)


@app.command("calibrate")
def calibrate(
    labels: Annotated[Path, typer.Option()],
    traces: Annotated[Path, typer.Option()],
    output: Annotated[Path, typer.Option()],
    config: ConfigOption = Path("configs/ollama.yaml"),
) -> None:
    """Fit conservative evidence bins using the calibration-business partition only."""
    try:
        cfg = load_config(config)
        cfg.preflight()
        artifact = fit_calibration(
            cfg.questions, labels, traces, inference_settings(cfg), seed=cfg.seed
        )
        save_calibration(artifact, output)
        emit(
            {
                "output": str(output),
                "traces_seen": artifact["traces_seen"],
                "buckets": artifact["buckets"],
                "official_ready": False,
            }
        )
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
    limit: Annotated[int | None, typer.Option(min=1)] = None,
    resume: bool = False,
) -> None:
    """Run dev prediction and a non-official rule-text scoring diagnostic."""
    try:
        cfg = load_config(config)
        if cfg.decision_policy == "calibrated" and cfg.calibration_artifact:
            artifact = json.loads(cfg.calibration_artifact.read_text(encoding="utf-8"))
            evaluation_questions = TrainKitAdapter(cfg.data_root).iter_questions(
                questions or cfg.questions
            )
            selected_questions = (
                islice(evaluation_questions, limit) if limit is not None else evaluation_questions
            )
            forbidden = set(artifact.get("business_split", {}).get("calibration", []))
            if any(q.business_id in forbidden for q in selected_questions):
                raise ValueError("Evaluation leakage: use businesses disjoint from calibration")
        _predict(config, questions, output, resume, limit)
        selected = (
            [r["id"] for r in read_predictions(output)]
            if limit is not None or questions is not None
            else None
        )
        emit(diagnostic_score(output, labels, selected))
    except (DatamateError, ValueError, OSError) as exc:
        fail(exc)


@app.command()
def score(
    predictions: Annotated[Path, typer.Option()],
    labels: Annotated[Path, typer.Option()],
) -> None:
    """Score existing predictions locally without running inference or reading test packages."""
    try:
        emit(diagnostic_score(predictions, labels))
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

import json
from pathlib import Path

from typer.testing import CliRunner

from datamate.cli import app


def test_predict_validate_resume(tmp_path: Path) -> None:
    runner = CliRunner()
    output = tmp_path / "predictions.jsonl"
    args = ["predict", "--config", "configs/default.yaml", "--output", str(output)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.stdout
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 3
    assert all(r["type"] == "abstain" for r in records)
    assert runner.invoke(app, args + ["--resume"]).exit_code == 0
    validation = runner.invoke(
        app, ["validate", "--config", "configs/default.yaml", "--predictions", str(output)]
    )
    assert validation.exit_code == 0, validation.stdout
    assert "LOCAL_FORMAT_PASS" in validation.stdout


def test_synthetic_smoke() -> None:
    result = CliRunner().invoke(app, ["test-fixtures"])
    assert result.exit_code == 0, result.stdout
    assert "SYNTHETIC_CPU_PASS" in result.stdout

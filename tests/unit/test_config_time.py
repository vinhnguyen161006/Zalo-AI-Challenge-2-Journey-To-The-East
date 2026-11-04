from pathlib import Path

import pytest
from typer.testing import CliRunner

from datamate.cli import app
from datamate.config import Config, load_config
from datamate.errors import InvalidInput, OfficialRulesUnverified
from datamate.semantics.rulebook import parse_rulebook
from datamate.semantics.time_engine import resolve_time


def test_config_paths_independent_of_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = Path(__file__).resolve().parents[2] / "configs/dev.yaml"
    monkeypatch.chdir(tmp_path)
    cfg = load_config(path)
    assert cfg.data_root.is_absolute()
    assert cfg.data_root.name == "data"
    assert cfg.questions.exists()


def test_official_fail_closed(config: Config) -> None:
    config.mode = "official"
    config.official.rules_verified = True
    with pytest.raises(OfficialRulesUnverified):
        config.preflight()


def test_config_cycle(tmp_path: Path) -> None:
    path = tmp_path / "loop.yaml"
    path.write_text("extends: loop.yaml", encoding="utf-8")
    with pytest.raises(InvalidInput, match="cycle"):
        load_config(path)


@pytest.mark.parametrize(
    ("question", "as_of", "start", "end"),
    [
        ("tuần rồi", "2026-01-01", "2025-12-22", "2025-12-29"),
        ("tuần này", "2026-01-04", "2025-12-29", "2026-01-05"),
        ("tháng này", "2024-02-29", "2024-02-01", "2024-03-01"),
        ("hôm qua", "2026-01-01", "2025-12-31", "2026-01-01"),
        ("7 ngày gần nhất", "2026-01-03", "2025-12-28", "2026-01-04"),
        ("Tết năm 2026", "2026-03-01", "2026-02-10", "2026-02-28"),
    ],
)
def test_half_open_time(config: Config, question: str, as_of: str, start: str, end: str) -> None:
    resolved = resolve_time(question, as_of, parse_rulebook(config.rulebook), config.calendar)
    assert (resolved.start_iso, resolved.end_exclusive_iso) == (start, end)
    assert not resolved.ambiguous


def test_missing_week_rule_and_lunar_year(config: Config) -> None:
    assert resolve_time("tuần rồi", "2026-01-01", [], config.calendar).ambiguous
    assert resolve_time("dịp Tết", "2026-01-01", [], config.calendar).ambiguous


def test_cli_help_and_official_gate() -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["--help"]).exit_code == 0
    result = runner.invoke(app, ["preflight", "--mode", "official"])
    assert result.exit_code == 1
    assert "BLOCKED_BY_OFFICIAL_KIT" in result.stdout


def test_model_manifest_does_not_claim_a_pass_without_weights() -> None:
    result = CliRunner().invoke(app, ["audit-models", "--config", "configs/models.yaml"])
    assert result.exit_code == 1
    assert "NOT_AUDITED" in result.stdout
    assert "UNVERIFIED" in result.stdout

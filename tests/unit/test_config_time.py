from pathlib import Path

import pytest
from typer.testing import CliRunner

from datamate.cli import app
from datamate.config import Config, load_config
from datamate.errors import InvalidInput, OfficialRulesUnverified
from datamate.semantics.question import language_hints
from datamate.semantics.rulebook import parse_rulebook
from datamate.semantics.time_engine import resolve_time
from datamate.types import RuleEvidence


def test_split_week_definition_overrides_unrelated_weekday(config: Config) -> None:
    rule = RuleEvidence(
        rule_id="week",
        source_path="synthetic",
        line_start=1,
        line_end=1,
        text="Tuần bắt đầu Chủ nhật. Báo cáo thường gửi thứ Hai.",
    )
    result = resolve_time("tuần trc", "2026-03-04", [rule], config.calendar)
    assert not result.ambiguous
    assert (result.start_iso, result.end_exclusive_iso) == ("2026-02-22", "2026-03-01")
    assert "week" in result.evidence
    conflicting = rule.model_copy(update={"rule_id": "other", "text": "Tuần bắt đầu thứ Hai"})
    assert resolve_time("tuần này", "2026-03-04", [rule, conflicting], config.calendar).ambiguous


def test_tet_dates_are_read_from_split_rule(config: Config, tmp_path: Path) -> None:
    calendar = tmp_path / "calendar.csv"
    calendar.write_text(
        "date,lunar_year,lunar_month,lunar_day\n2026-02-12,2025,12,25\n2026-02-22,2026,1,5\n",
        encoding="utf-8",
    )
    rule = RuleEvidence(
        rule_id="tet",
        source_path="synthetic",
        line_start=1,
        line_end=1,
        text="Tết năm Y: từ 25 tháng Chạp đến hết mùng 5 tháng Giêng.",
    )
    result = resolve_time("Tết năm 2026", "2026-03-04", [rule], calendar)
    assert not result.ambiguous
    assert (result.start_iso, result.end_exclusive_iso) == ("2026-02-12", "2026-02-23")
    assert "tet" in result.evidence
    assert resolve_time("Tết năm 2026", "2026-03-04", [], calendar).ambiguous


def test_config_paths_independent_of_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = Path(__file__).resolve().parents[2] / "configs/default.yaml"
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
    fixture_config = Path(__file__).resolve().parents[2] / "configs/default.yaml"
    result = runner.invoke(
        app, ["preflight", "--config", str(fixture_config), "--mode", "official"]
    )
    assert result.exit_code == 1
    assert "BLOCKED_BY_OFFICIAL_KIT" in result.stdout


def test_model_manifest_does_not_claim_a_pass_without_weights() -> None:
    result = CliRunner().invoke(app, ["audit-models", "--config", "configs/models.yaml"])
    assert result.exit_code == 1
    assert "NOT_AUDITED" in result.stdout
    assert "UNVERIFIED" in result.stdout


@pytest.mark.parametrize(
    ("question", "start", "end"),
    [
        ("ngày 29/02/2024", "2024-02-29", "2024-03-01"),
        ("từ 28/02/2024 đến 01/03/2024", "2024-02-28", "2024-03-02"),
        ("quý 4/2025", "2025-10-01", "2026-01-01"),
        ("quý trước", "2026-04-01", "2026-07-01"),
        ("năm 2025", "2025-01-01", "2026-01-01"),
        ("tháng 12", "2025-12-01", "2026-01-01"),
    ],
)
def test_extended_periods(config: Config, question: str, start: str, end: str) -> None:
    rule = RuleEvidence(
        rule_id="year",
        source_path="synthetic",
        line_start=1,
        line_end=1,
        text="Tháng, quý không ghi năm: kỳ chưa xảy ra lấy năm trước",
    )
    resolved = resolve_time(question, "2026-08-10", [rule], config.calendar)
    assert not resolved.ambiguous
    assert (resolved.start_iso, resolved.end_exclusive_iso) == (start, end)


def test_month_comparison_clips_short_month_only_when_rule_supports_it(config: Config) -> None:
    question = "tháng này so với tháng trước"
    rule = RuleEvidence(
        rule_id="comparison",
        source_path="synthetic",
        line_start=1,
        line_end=1,
        text="So tháng này với tháng trước lấy cùng số ngày",
    )
    resolved = resolve_time(question, "2024-03-31", [rule], config.calendar)
    assert not resolved.ambiguous
    assert (resolved.comparison_start_iso, resolved.comparison_end_exclusive_iso) == (
        "2024-02-01",
        "2024-03-01",
    )
    assert resolve_time(question, "2024-03-31", [], config.calendar).ambiguous


@pytest.mark.parametrize(
    "question", ["Công nợ đến hết ngày 12/10/2025", "Tồn kho tính đến hôm nay"]
)
def test_snapshot_uses_full_history_cutoff_when_cited_rule_supports_it(
    config: Config, question: str
) -> None:
    rule = RuleEvidence(
        rule_id="snapshot",
        source_path="synthetic",
        line_start=1,
        line_end=1,
        text="Tính từ đầu dữ liệu đến hết ngày đó",
    )
    result = resolve_time(question, "2025-10-12", [rule], config.calendar)
    assert not result.ambiguous
    assert result.start_iso is None and result.end_exclusive_iso == "2025-10-13"


SAME_DAYS = RuleEvidence(
    rule_id="same_days",
    source_path="synthetic",
    line_start=1,
    line_end=1,
    text="Tháng này so với tháng trước thì lấy cùng số ngày của tháng trước",
)


@pytest.mark.parametrize(
    ("question", "primary", "comparison"),
    [
        (
            "dthu tháng 3/2026 hơn hay kém th trước bn %",
            ("2026-03-01", "2026-04-01"),
            ("2026-02-01", "2026-03-01"),
        ),
        (
            "So với tháng liền trước, doanh thu tháng 3 năm 2026 thay đổi bao nhiêu %?",
            ("2026-03-01", "2026-04-01"),
            ("2026-02-01", "2026-03-01"),
        ),
        (
            "so vs thang truoc do, dthu thang 5/2026 tang hay giam may phan tram",
            ("2026-05-01", "2026-06-01"),
            ("2026-04-01", "2026-05-01"),
        ),
        (
            "Doanh thu tháng 4/2026 so với tháng 4/2025 tăng hay giảm bao nhiêu %?",
            ("2026-04-01", "2026-05-01"),
            ("2025-04-01", "2025-05-01"),
        ),
        (
            "Th 6/26 dthu so với th 6/25 tăng hay giảm mấy %?",
            ("2026-06-01", "2026-07-01"),
            ("2025-06-01", "2025-07-01"),
        ),
        (
            "Dthu T6/2025 so tháng liền trước tăng hay giảm mấy %?",
            ("2025-06-01", "2025-07-01"),
            ("2025-05-01", "2025-06-01"),
        ),
        (
            "doanh thu từ đầu tháng này đến hết bữa nay hơn hay kém cùng kỳ tháng rồi bnhiu %",
            ("2026-03-01", "2026-03-18"),
            ("2026-02-01", "2026-02-18"),
        ),
        (
            "tuần này so với tuần trước doanh thu hơn kém bao nhiêu",
            ("2026-03-16", "2026-03-18"),
            ("2026-03-09", "2026-03-11"),
        ),
    ],
)
def test_comparison_windows(
    config: Config, question: str, primary: tuple[str, str], comparison: tuple[str, str]
) -> None:
    rules = [*parse_rulebook(config.rulebook), SAME_DAYS]
    resolved = resolve_time(question, "2026-03-17", rules, config.calendar)
    assert not resolved.ambiguous
    assert (resolved.start_iso, resolved.end_exclusive_iso) == primary
    assert (resolved.comparison_start_iso, resolved.comparison_end_exclusive_iso) == comparison


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("thangs 4/2025 tieenf bans dduowcj bao nhieu", "thang 4/2025 tien ban duoc bao nhieu"),
        ("check gium dthu nhoms hang", "check gium doanh thu nhom hang"),
        ("Plusssz Max so vs thang truoc", "Plusssz Max so với thang truoc"),
        ("revenue 9/2025 la bao nhieu", "doanh thu tháng 9/2025 la bao nhieu"),
        ("6-2026 bị huỷ mấy đơn", "tháng 6/2026 bị huỷ mấy đơn"),
        ("tháng 11/25 so với 15/02/2026", "tháng 11/2025 so với 15/02/2026"),
        ("doanh thu quý 2/2026 là bao nhiêu", "doanh thu quý 2/2026 là bao nhiêu"),
    ],
)
def test_language_hints_repair_typing_and_bare_months(raw: str, expected: str) -> None:
    assert language_hints(raw)["expanded_question"] == expected


@pytest.mark.parametrize(
    "question",
    [
        "Thang 2/26 so voi cung thang nam truoc, doanh thu thay doi bao nhieu %?",
        "2/26 so với cùng kỳ năm trước, doanh thu lên hay xuống bao nhiêu % vậy?",
        "Doanh thu tháng 2/2026 so với cùng tháng năm ngoái tăng hay giảm mấy %",
    ],
)
def test_same_month_last_year_comparison(config: Config, question: str) -> None:
    resolved = resolve_time(question, "2026-03-17", parse_rulebook(config.rulebook), config.calendar)
    assert not resolved.ambiguous
    assert (resolved.start_iso, resolved.end_exclusive_iso) == ("2026-02-01", "2026-03-01")
    assert (resolved.comparison_start_iso, resolved.comparison_end_exclusive_iso) == (
        "2025-02-01",
        "2025-03-01",
    )

"""Config inheritance, environment expansion, and official-mode gates."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field

from datamate.errors import InvalidInput, OfficialRulesUnverified
from datamate.types import Schema


class Limits(Schema):
    query_timeout_seconds: float = Field(default=10, gt=0, le=3600)
    max_wallclock_seconds: float = Field(default=60, gt=0, le=3600)
    max_model_calls: int = Field(default=6, ge=1, le=100)
    max_candidates: int = Field(default=4, ge=1, le=8)
    max_repairs: int = Field(default=1, ge=0, le=2)
    max_result_rows: int = Field(default=10000, ge=1)
    max_result_bytes: int = Field(default=2000000, ge=100)
    memory_mb: int = Field(default=512, ge=64)
    max_source_bytes: int = Field(default=200000000, ge=1)
    max_source_files: int = Field(default=200, ge=1)
    max_dsl_source_rows: int = Field(default=500000, ge=1)
    max_context_bytes: int = Field(default=512000, ge=1000)
    slow_candidates: int = Field(default=3, ge=1, le=8)
    grounding_limit: int = Field(default=8, ge=1)
    grounding_scan_limit: int = Field(default=5000, ge=1)
    grounding_min_length: int = Field(default=3, ge=1)
    grounding_score_threshold: float = Field(default=70, ge=0, le=100)
    grounding_ambiguity_margin: float = Field(default=5, ge=0, le=100)


class Models(Schema):
    backend: Literal["disabled", "local"] = "disabled"
    planner_endpoint: str = "http://127.0.0.1:8001/v1/chat/completions"
    reasoner_endpoint: str = "http://127.0.0.1:8000/v1/chat/completions"
    planner_model: str = "Qwen/Qwen3.5-4B"
    reasoner_model: str = "Qwen/Qwen3.5-9B"
    max_output_tokens: int = Field(default=4096, ge=1)
    planner_revision: str | None = None
    reasoner_revision: str | None = None


class Official(Schema):
    rules_verified: bool = False
    validator_path: str | None = None
    parameter_cap: int | None = None
    checkpoint_audit: str | None = None
    scoring_verified: bool = False


class Config(Schema):
    mode: Literal["dev", "official"] = "dev"
    adapter: Literal["synthetic", "train"] = "synthetic"
    data_root: Path
    rulebook: Path
    calendar: Path
    questions: Path
    output_root: Path
    seed: int = 7
    models: Models = Field(default_factory=Models)
    limits: Limits = Field(default_factory=Limits)
    official: Official = Field(default_factory=Official)

    def preflight(self) -> dict[str, Any]:
        missing = [
            str(p)
            for p in (self.data_root, self.rulebook, self.calendar, self.questions)
            if not p.exists()
        ]
        if missing:
            raise InvalidInput(f"Missing inputs: {missing}")
        if self.mode == "official":
            raise OfficialRulesUnverified(
                "BLOCKED_BY_OFFICIAL_KIT: official validator, deployment guidance, "
                "parameter policy and verified adapter integration are required."
            )
        return {
            "mode": self.mode,
            "adapter": self.adapter,
            "status": "DEV_INPUTS_OK",
            "official_ready": False,
        }


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        result[key] = (
            _merge(result[key], value)
            if isinstance(value, dict) and isinstance(result.get(key), dict)
            else value
        )
    return result


def _read(path: Path, seen: set[Path]) -> dict[str, Any]:
    path = path.resolve()
    if path in seen:
        raise InvalidInput("Configuration inheritance cycle")
    seen.add(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise InvalidInput("Configuration must be a YAML mapping")
    parent = data.pop("extends", None)
    if parent:
        data = _merge(_read(path.parent / parent, seen), data)
    for key in ("data_root", "rulebook", "calendar", "questions", "output_root"):
        if key in data and not Path(str(data[key])).is_absolute():
            expanded = os.path.expandvars(str(data[key]))
            if "$" in expanded:
                raise InvalidInput(f"Unresolved environment variable: {key}")
            data[key] = str((path.parent / expanded).resolve())
    return data


def load_config(path: Path, mode: str | None = None) -> Config:
    data = _read(path, set())
    if mode is not None:
        data["mode"] = mode
    return Config.model_validate(data)

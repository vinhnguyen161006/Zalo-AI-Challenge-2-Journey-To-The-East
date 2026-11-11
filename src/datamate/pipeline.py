"""Bounded Planner -> SQLCoder/PandasCoder -> Execute -> Verify orchestration."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from datamate.adapters.submission_writer import json_line, serialize, validate_record
from datamate.config import Config
from datamate.data.grounder import ground
from datamate.data.ingest import ingest_business, sha256_file
from datamate.decision.calibration import (
    calibrated_confidence,
    inference_settings,
    runtime_signature,
)
from datamate.decision.policy import abstain, execution_baseline
from datamate.errors import DatamateError, InvalidModelOutput, ResourceExceeded
from datamate.execution.dataops_dsl import DataOpsCandidate
from datamate.execution.sql_executor import execute
from datamate.execution.templates import ranking_template, scalar_template
from datamate.llm.agents import ask, evidence_context, qualify_plan_columns, validate_plan
from datamate.llm.client import ModelClient, OllamaModelClient, VLLMModelClient
from datamate.routing.budgets import BudgetManager
from datamate.routing.router import route
from datamate.semantics.compiled_rules import (
    bind_resolved_time_filters,
    compile_period_constraint,
    compile_revenue_measure,
    compile_sales_validity,
)
from datamate.semantics.rulebook import parse_rulebook, retrieve_rules
from datamate.semantics.time_engine import resolve_time
from datamate.types import (
    Catalog,
    ExecutionResult,
    InternalDecision,
    PlanIR,
    QuestionRecord,
    SqlCandidate,
)
from datamate.verification.canonicalize import answers_agree
from datamate.verification.coverage import uncovered_period
from datamate.verification.differential import challenge_agreement
from datamate.verification.invariants import answer_value, verify

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        config: Config,
        planner: ModelClient | None = None,
        reasoner: ModelClient | None = None,
        *,
        trace_directory: Path | None = None,
    ):
        self.config = config
        self.trace_directory = trace_directory or config.output_root / "logs"
        self.catalogs: dict[str, Catalog] = {}
        self.rules = parse_rulebook(config.rulebook)
        self.rules_sha = sha256_file(config.rulebook)
        self.calendar_sha = sha256_file(config.calendar)
        self.last_trace: dict[str, Any] = {}
        self.runtime_signature = runtime_signature(inference_settings(config))
        self.calibration = (
            json.loads(config.calibration_artifact.read_text(encoding="utf-8"))
            if config.calibration_artifact
            else None
        )
        if config.models.backend == "local":
            planner = planner or VLLMModelClient(
                config.models.planner_endpoint,
                config.models.planner_model,
                config.models.max_output_tokens,
            )
            reasoner = reasoner or VLLMModelClient(
                config.models.reasoner_endpoint,
                config.models.reasoner_model,
                config.models.max_output_tokens,
            )
        elif config.models.backend == "ollama":
            planner = planner or OllamaModelClient(
                config.models.planner_endpoint,
                config.models.planner_model,
                config.models.max_output_tokens,
                config.models.context_tokens,
                config.seed,
                config.models.thinking,
            )
            reasoner = reasoner or OllamaModelClient(
                config.models.reasoner_endpoint,
                config.models.reasoner_model,
                config.models.max_output_tokens,
                config.models.context_tokens,
                config.seed,
                config.models.thinking,
            )
        self.planner, self.reasoner = planner, reasoner

    def catalog(self, business_id: str) -> Catalog:
        if business_id not in self.catalogs:
            self.catalogs[business_id] = ingest_business(
                self.config.data_root,
                business_id,
                self.config.output_root / "cache",
                self.config.limits,
                rulebook=self.config.rulebook,
            )
        return self.catalogs[business_id]

    def solve(self, q: QuestionRecord) -> InternalDecision:
        started = time.monotonic()
        logger.info("query_started", extra={"question_id": q.id})
        for client in (self.planner, self.reasoner):
            if isinstance(client, VLLMModelClient):
                client.last_output = None
        budget = BudgetManager(self.config.limits)
        trace: dict[str, Any] = {
            "question_id": q.id,
            "business_id": q.business_id,
            "runtime_signature": self.runtime_signature,
            "candidate_summaries": [],
            "verification": [],
            "rulebook_fingerprint": self.rules_sha,
            "calendar_fingerprint": self.calendar_sha,
            "model_revisions": {
                "planner": self.config.models.planner_revision,
                "reasoner": self.config.models.reasoner_revision,
            },
        }
        decision = abstain(q.id, "no_evidence")
        try:
            if not self.planner or not self.reasoner:
                decision = abstain(q.id, "model_backend_disabled")
            else:
                catalog = self.catalog(q.business_id)
                trace["input_fingerprint"] = catalog.fingerprint
                rules = retrieve_rules(q.question, self.rules)
                time_result = resolve_time(
                    q.question, q.as_of_date, self.rules, self.config.calendar
                )
                entities = ground(q.question, catalog, self.config.limits)
                original = evidence_context(q, catalog, rules, time_result, entities)
                payload = dict(original)
                plan = None
                plan_attempts = 3 if self.config.planner_critic else 2
                for attempt in range(plan_attempts):
                    try:
                        client = self.reasoner if attempt == 2 else self.planner
                        proposed = ask(client, "planner", payload, PlanIR, budget)
                        proposed = qualify_plan_columns(proposed, catalog)
                        proposed, bound_time = bind_resolved_time_filters(
                            proposed, catalog, time_result
                        )
                        if bound_time:
                            trace["bound_time_filter_fields"] = bound_time
                        proposed, measures = compile_revenue_measure(proposed, q, catalog, rules)
                        if measures:
                            trace["compiled_metric_constraints"] = measures
                        proposed, compiled = compile_sales_validity(proposed, q, catalog, rules)
                        proposed, temporal = compile_period_constraint(
                            proposed, q, catalog, time_result
                        )
                        if compiled:
                            trace["compiled_rule_constraints"] = compiled
                        if temporal:
                            trace["compiled_time_constraints"] = temporal
                        errors = validate_plan(proposed, q, catalog, rules)
                        if errors:
                            raise InvalidModelOutput(",".join(errors))
                        plan = proposed
                        if attempt == 2:
                            trace["planner_recovery"] = (
                                "reasoner_checkpoint_after_two_invalid_plans"
                            )
                        break
                    except InvalidModelOutput as exc:
                        if attempt == plan_attempts - 1:
                            raise
                        payload["validation_feedback"] = str(exc)
                assert plan is not None
                if self.config.log_programs:
                    trace["validated_plan"] = plan.model_dump()
                if plan.unknowns:
                    raise InvalidModelOutput(
                        "Unresolved source evidence: " + "; ".join(plan.unknowns)
                    )
                coverage = uncovered_period(
                    plan,
                    catalog,
                    time_result,
                    self.rules,
                    self.config.limits,
                    budget.remaining_seconds(),
                )
                if coverage:
                    trace["unanswerable_evidence"] = coverage
                    decision = InternalDecision(
                        question_id=q.id,
                        status="unanswerable",
                        reason_codes=["requested_period_fully_outside_source_coverage"],
                    )
                    return self._finish(q, decision, trace, started, budget)
                path, reasons = route(plan, time_result)
                trace.update(route=path, route_reasons=reasons)
                independent = None
                needs_independent = (
                    path == "SLOW"
                    or self.config.decision_policy in {"execution_baseline", "calibrated"}
                    and self.config.require_independent_agreement
                )
                if (
                    self.config.pandas_first
                    and needs_independent
                    and self.config.limits.max_candidates > 1
                ):
                    budget.candidate()
                    independent = self._run_pandas(original, catalog, budget)
                candidates = []
                slow_total = min(
                    self.config.limits.slow_candidates, self.config.limits.max_candidates - 1
                )
                total = 1 if path == "FAST" else slow_total
                strategies = ["direct", "ctes", "preaggregate"]
                index = 0
                total = max(1, total)
                while index < total:
                    budget.candidate()
                    template = (
                        scalar_template(plan, catalog, time_result)
                        or ranking_template(plan, catalog, time_result)
                        if self.config.use_verified_templates and index == 0
                        else None
                    )
                    candidate = template or ask(
                        self.reasoner,
                        "sql_coder",
                        {
                            **original,
                            "validated_plan": plan.model_dump(),
                            "strategy": strategies[index % len(strategies)],
                        },
                        SqlCandidate,
                        budget,
                    )
                    hashes: set[str] = set()
                    for repair in range(self.config.limits.max_repairs + 1):
                        digest = hashlib.sha256(candidate.sql.encode()).hexdigest()
                        if digest in hashes:
                            break
                        hashes.add(digest)
                        try:
                            result = execute(
                                candidate, catalog, self.config.limits, budget.remaining_seconds()
                            )
                            report = verify(candidate, result, plan, catalog, rules, time_result)
                            candidates.append((candidate, result, report))
                            trace["candidate_summaries"].append(
                                {
                                    "id": candidate.candidate_id,
                                    "program_hash": result.program_hash,
                                    "status": "ok",
                                }
                            )
                            trace["verification"].append(report.model_dump())
                            if self.config.log_programs:
                                trace.setdefault("executed_candidates", []).append(
                                    {
                                        "candidate": candidate.model_dump(),
                                        "result": result.model_dump(),
                                    }
                                )
                            if report.hard_failures:
                                raise InvalidModelOutput(
                                    "Verification failed: " + ", ".join(report.hard_failures)
                                )
                            break
                        except DatamateError as exc:
                            trace["candidate_summaries"].append(
                                {"id": candidate.candidate_id, "status": type(exc).__name__}
                            )
                            if repair == self.config.limits.max_repairs:
                                break
                            candidate = ask(
                                self.reasoner,
                                "sql_coder",
                                {
                                    **original,
                                    "validated_plan": plan.model_dump(),
                                    "failed_program": candidate.sql,
                                    "error_type": type(exc).__name__,
                                    "execution_error": str(exc),
                                },
                                SqlCandidate,
                                budget,
                            )
                    index += 1
                    if (
                        self.config.adaptive_candidates
                        and independent is not None
                        and not plan.risk_flags
                        and not time_result.ambiguous
                    ):
                        independent_candidate, independent_result = independent
                        if any(
                            not report.hard_failures
                            and candidate.answer_type == independent_candidate.answer_type
                            and answers_agree(
                                answer_value(result, candidate.answer_type),
                                answer_value(independent_result, independent_candidate.answer_type),
                                candidate.answer_type,
                            )
                            for candidate, result, report in candidates
                        ):
                            trace["candidate_stop_reason"] = (
                                "verified_candidate_matches_independent_program"
                            )
                            break
                    if (
                        path == "FAST"
                        and not any(not report.hard_failures for _, _, report in candidates)
                        and budget.candidates < self.config.limits.max_candidates
                    ):
                        path = "SLOW"
                        trace.update(
                            route="SLOW",
                            route_reasons=[*reasons, "prior_execution_or_verification_failure"],
                        )
                        total = max(total, slow_total)
                disagreement = False
                if independent is not None or (
                    (
                        path == "SLOW"
                        or self.config.decision_policy in {"execution_baseline", "calibrated"}
                        and self.config.require_independent_agreement
                    )
                    and budget.candidates < self.config.limits.max_candidates
                ):
                    if independent is None:
                        budget.candidate()
                        independent = self._run_pandas(original, catalog, budget)
                    dsl, dsl_result = independent
                    trace["independent_dsl"] = {
                        "program_hash": dsl_result.program_hash,
                        "status": "ok",
                    }
                    if self.config.log_programs:
                        trace["independent_dsl"].update(
                            candidate=dsl.model_dump(), result=dsl_result.model_dump()
                        )
                    comparisons = [
                        candidate.answer_type == dsl.answer_type
                        and answers_agree(
                            answer_value(result, candidate.answer_type),
                            answer_value(dsl_result, dsl.answer_type),
                            candidate.answer_type,
                        )
                        for candidate, result, report in candidates
                        if not report.hard_failures
                    ]
                    disagreement = any(not matches for matches in comparisons)
                    trace["cross_paradigm_agreement"] = all(comparisons) if comparisons else None
                    if (
                        self.config.differential_verification
                        and trace["cross_paradigm_agreement"] is True
                    ):
                        for sql_candidate, _, report in candidates:
                            if report.hard_failures:
                                continue
                            challenge = challenge_agreement(
                                sql_candidate,
                                dsl,
                                plan,
                                catalog,
                                time_result,
                                self.config.limits,
                                self.config.output_root / "challenges",
                                budget.remaining_seconds(),
                            )
                            trace.setdefault("differential_verification", []).append(challenge)
                            if not challenge["passed"]:
                                trace["cross_paradigm_agreement"] = False
                                disagreement = True
                                break
                failures = sorted({f for _, _, report in candidates for f in report.hard_failures})
                if disagreement:
                    failures.append("cross_paradigm_disagreement")
                if self.config.decision_policy in {"execution_baseline", "calibrated"}:
                    decision = execution_baseline(
                        plan,
                        candidates,
                        trace.get("cross_paradigm_agreement"),
                        self.config.require_independent_agreement,
                    )
                    if self.config.decision_policy == "calibrated" and decision.status == "answer":
                        probability = calibrated_confidence(self.calibration or {}, trace)
                        if probability is None or probability <= 0.5:
                            decision = abstain(q.id, "insufficient_calibration_evidence")
                        else:
                            decision.confidence = probability
                            decision.reason_codes = ["empirical_lower_bound_above_pa_threshold"]
                else:
                    decision = abstain(
                        q.id,
                        *(failures or ["uncalibrated_policy", "semantic_verification_incomplete"]),
                    )
        except (DatamateError, ValueError, OSError) as exc:
            if self.config.log_programs:
                trace["error_detail"] = str(exc)
                trace["last_planner_output"] = getattr(self.planner, "last_output", None)
                trace["last_reasoner_output"] = getattr(self.reasoner, "last_output", None)
            logger.warning(
                "query_fallback", extra={"question_id": q.id, "error_type": type(exc).__name__}
            )
            decision = abstain(q.id, type(exc).__name__)
        if budget.remaining_seconds() <= 0:
            decision = abstain(q.id, ResourceExceeded.__name__)
        return self._finish(q, decision, trace, started, budget)

    def _run_pandas(
        self, original: dict[str, Any], catalog: Catalog, budget: BudgetManager
    ) -> tuple[DataOpsCandidate, ExecutionResult]:
        assert self.planner is not None
        dsl = ask(self.planner, "dsl_coder", original, DataOpsCandidate, budget)
        for repair in range(self.config.limits.max_repairs + 1):
            try:
                result = execute(dsl, catalog, self.config.limits, budget.remaining_seconds())
                validate_record(
                    {
                        "id": original["question"]["id"],
                        "type": dsl.answer_type,
                        "answer": answer_value(result, dsl.answer_type),
                    }
                )
                return dsl, result
            except DatamateError as exc:
                if repair == self.config.limits.max_repairs:
                    raise
                dsl = ask(
                    self.planner,
                    "dsl_coder",
                    {**original, "own_failed_program": dsl.model_dump(), "own_error": str(exc)},
                    DataOpsCandidate,
                    budget,
                )
        raise InvalidModelOutput("No independent pandas program")

    def _finish(
        self,
        q: QuestionRecord,
        decision: InternalDecision,
        trace: dict[str, Any],
        started: float,
        budget: BudgetManager,
    ) -> InternalDecision:
        trace.update(
            final_answer=serialize(decision),
            decision=decision.status,
            decision_reason=decision.reason_codes,
            latency_ms=(time.monotonic() - started) * 1000,
            model_calls=budget.model_calls,
            model_generations=budget.generations,
        )
        self.last_trace = trace
        trace_dir = self.trace_directory
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_name = hashlib.sha256(q.id.encode()).hexdigest() + ".json"
        (trace_dir / trace_name).write_text(json_line(trace), encoding="utf-8")
        logger.info(
            "query_completed",
            extra={
                "question_id": q.id,
                "status": decision.status,
                "elapsed_seconds": round(trace["latency_ms"] / 1000, 2),
                "model_calls": budget.model_calls,
            },
        )
        return decision

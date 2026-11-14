"""Bounded Planner -> SQLCoder/PandasCoder -> Execute -> Verify orchestration."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import duckdb

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
from datamate.semantics.sales_filters import sales_intent
from datamate.semantics.sales_model import (
    ROW_MEASURES,
    SALES_ROUTE,
    SalesModel,
    family_kind,
    infer_sales_model,
    period_outside_orders,
    program_sql,
    sales_answer,
    sales_programs,
)
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
CALIBRATED_ANSWER_THRESHOLD = 0.5


SALES_PROGRAM_SOURCE = "src/datamate/semantics/sales_model.py::sales_sql"


def executed_program(trace: dict[str, Any]) -> tuple[Any, Any, str | None]:
    """The executed SQL and its output for the per-question log; models emit no reasoning."""
    executed = trace.get("executed_candidates") or []
    if not executed:
        return None, None, None
    codes = [item["candidate"]["sql"] for item in executed]
    outputs = [item["result"]["rows"] for item in executed]
    if trace.get("route") == SALES_ROUTE:
        values = [rows[0][0] for rows in outputs]
        if len(codes) == 1:
            return codes[0], values[0], SALES_PROGRAM_SOURCE
        return codes, values, SALES_PROGRAM_SOURCE
    if len(codes) == 1:
        return codes[0], outputs[0], None
    return codes, outputs, None


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
        self.sales_models: dict[str, SalesModel | None] = {}
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
            sales = self._try_sales(q, trace) if self.config.sales_model else None
            if sales is not None:
                return self._finish(q, sales, trace, started, budget)
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
                pandas_attempted = False
                if (
                    self.config.pandas_first
                    and needs_independent
                    and self.config.limits.max_candidates > 1
                ):
                    budget.candidate()
                    pandas_attempted = True
                    independent = self._run_pandas(original, catalog, budget, trace)
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
                if (
                    independent is None
                    and not pandas_attempted
                    and (
                        path == "SLOW"
                        or self.config.decision_policy in {"execution_baseline", "calibrated"}
                        and self.config.require_independent_agreement
                    )
                    and budget.candidates < self.config.limits.max_candidates
                ):
                    budget.candidate()
                    independent = self._run_pandas(original, catalog, budget, trace)
                if independent is not None:
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
                        if probability is None or probability <= CALIBRATED_ANSWER_THRESHOLD:
                            decision = abstain(q.id, "insufficient_calibration_evidence")
                        else:
                            decision.confidence = probability
                            decision.reason_codes = ["empirical_lower_bound_above_pa_threshold"]
                else:
                    decision = abstain(
                        q.id,
                        *(failures or ["uncalibrated_policy", "semantic_verification_incomplete"]),
                    )
        except (DatamateError, ValueError, OSError, duckdb.Error) as exc:
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

    def _try_sales(self, q: QuestionRecord, trace: dict[str, Any]) -> InternalDecision | None:
        """A failed compiled program falls back to the model route; it never fails the batch."""
        try:
            return self._solve_sales(q, trace)
        except (DatamateError, duckdb.Error) as exc:
            for key in ("route", "sales_model", "executed_candidates"):
                trace.pop(key, None)
            trace["sales_model_error"] = type(exc).__name__
            return None

    def _solve_sales(self, q: QuestionRecord, trace: dict[str, Any]) -> InternalDecision | None:
        """Answer a sales question with the compiled rulebook section-1 program."""
        catalog = self.catalog(q.business_id)
        model = self._sales_model(catalog)
        intent = sales_intent(q.question, catalog, model) if model else None
        if model is None or intent is None:
            return None
        family = intent.family
        window = resolve_time(q.question, q.as_of_date, self.rules, self.config.calendar)
        programs = sales_programs(intent, window)
        if programs is None:
            return None
        trace.update(
            route=SALES_ROUTE,
            input_fingerprint=catalog.fingerprint,
            sales_model={
                "family": family,
                "kind": family_kind(family),
                "filter": asdict(intent.sales_filter) if intent.sales_filter else None,
                "window": window.model_dump(),
                **asdict(model),
            },
        )
        if any(period_outside_orders(model, catalog, p.start, p.end) for p in programs):
            decision = InternalDecision(
                question_id=q.id,
                status="unanswerable",
                reason_codes=["requested_period_fully_outside_source_coverage"],
            )
            return self._sales_policy(q, decision, trace)
        statements = [program_sql(model, p) for p in programs]
        if any(sql is None for sql in statements):
            return None
        values = [
            self._run_sales_sql(catalog, family, str(sql), trace, p.measure in ROW_MEASURES)
            for sql, p in zip(statements, programs, strict=True)
        ]
        answer = sales_answer(intent, values)
        if answer is None:
            return abstain(q.id, "reference_period_has_zero_value")
        decision = InternalDecision(
            question_id=q.id,
            status="answer",
            answer_type=answer[0],
            value=answer[1],
            reason_codes=["sales_model_rulebook_section_1"],
        )
        return self._sales_policy(q, decision, trace)

    def _sales_model(self, catalog: Catalog) -> SalesModel | None:
        if catalog.fingerprint not in self.sales_models:
            self.sales_models[catalog.fingerprint] = infer_sales_model(catalog)
        return self.sales_models[catalog.fingerprint]

    def _run_sales_sql(
        self, catalog: Catalog, family: str, sql: str, trace: dict[str, Any], rows: bool
    ) -> object:
        candidate = SqlCandidate(
            candidate_id=f"sales_{family}", sql=sql, strategy="direct", answer_type="number"
        )
        result = execute(
            candidate, catalog, self.config.limits, self.config.limits.max_wallclock_seconds
        )
        trace.setdefault("executed_candidates", []).append(
            {"candidate": candidate.model_dump(), "result": result.model_dump()}
        )
        return result.rows if rows else answer_value(result, "number")

    def _sales_policy(
        self, q: QuestionRecord, decision: InternalDecision, trace: dict[str, Any]
    ) -> InternalDecision:
        if self.config.decision_policy == "abstain":
            return abstain(q.id, "uncalibrated_policy")
        if self.config.decision_policy == "calibrated":
            probability = calibrated_confidence(self.calibration or {}, trace)
            if probability is None or probability <= CALIBRATED_ANSWER_THRESHOLD:
                return abstain(q.id, "insufficient_calibration_evidence")
            decision.confidence = probability
        return decision

    def _run_pandas(
        self,
        original: dict[str, Any],
        catalog: Catalog,
        budget: BudgetManager,
        trace: dict[str, Any],
    ) -> tuple[DataOpsCandidate, ExecutionResult] | None:
        """Run the independent branch; its failure is evidence, never a fatal error."""
        assert self.planner is not None
        dsl: DataOpsCandidate | None = None
        try:
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
                        {
                            **original,
                            "own_failed_program": dsl.model_dump(),
                            "own_error": str(exc),
                        },
                        DataOpsCandidate,
                        budget,
                    )
        except DatamateError as exc:
            trace["independent_dsl"] = {"status": type(exc).__name__, "error": str(exc)[:500]}
            if dsl is not None and self.config.log_programs:
                trace["independent_dsl"]["candidate"] = dsl.model_dump()
        return None

    def _finish(
        self,
        q: QuestionRecord,
        decision: InternalDecision,
        trace: dict[str, Any],
        started: float,
        budget: BudgetManager,
    ) -> InternalDecision:
        latency_ms = (time.monotonic() - started) * 1000
        answer = serialize(decision)
        code, output, source = executed_program(trace)
        trace.update(
            final_answer=answer,
            decision=decision.status,
            decision_reason=decision.reason_codes,
            latency_ms=latency_ms,
            model_calls=budget.model_calls,
            model_generations=budget.generations,
            id=q.id,
            answer=answer,
            elapsed_ms=int(latency_ms),
            thinking=None,
            code=code,
            code_source=source,
            output=output,
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

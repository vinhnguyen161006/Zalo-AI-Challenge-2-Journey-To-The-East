"""Bounded Plan -> SQL/DataOps -> Execute -> Verify orchestration."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from datamate.config import Config
from datamate.data.ingest import ingest_business, sha256_file
from datamate.decision.policy import abstain
from datamate.errors import DatamateError, InvalidModelOutput, ResourceExceeded
from datamate.execution.dataops_dsl import DataOpsCandidate
from datamate.execution.sql_executor import execute
from datamate.llm.agents import ask, evidence_context, validate_plan
from datamate.llm.client import ModelClient, VLLMModelClient
from datamate.routing.budgets import BudgetManager
from datamate.routing.router import route
from datamate.semantics.rulebook import parse_rulebook, retrieve_rules
from datamate.semantics.time_engine import resolve_time
from datamate.types import Catalog, InternalDecision, PlanIR, QuestionRecord, SqlCandidate
from datamate.verification.canonicalize import canonicalize
from datamate.verification.invariants import answer_value, verify

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        config: Config,
        planner: ModelClient | None = None,
        reasoner: ModelClient | None = None,
    ):
        self.config = config
        self.catalogs: dict[str, Catalog] = {}
        self.rules = parse_rulebook(config.rulebook)
        self.rules_sha = sha256_file(config.rulebook)
        self.calendar_sha = sha256_file(config.calendar)
        self.last_trace: dict[str, Any] = {}
        if config.models.backend == "local":
            planner = planner or VLLMModelClient(
                config.models.planner_endpoint, config.models.planner_model
            )
            reasoner = reasoner or VLLMModelClient(
                config.models.reasoner_endpoint, config.models.reasoner_model
            )
        self.planner, self.reasoner = planner, reasoner

    def catalog(self, business_id: str) -> Catalog:
        if business_id not in self.catalogs:
            self.catalogs[business_id] = ingest_business(
                self.config.data_root,
                business_id,
                self.config.output_root / "cache",
                self.config.limits,
            )
        return self.catalogs[business_id]

    def solve(self, q: QuestionRecord) -> InternalDecision:
        started = time.monotonic()
        budget = BudgetManager(self.config.limits)
        trace: dict[str, Any] = {
            "question_id": q.id,
            "business_id": q.business_id,
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
                original = evidence_context(q, catalog, rules, time_result)
                payload = dict(original)
                plan = None
                for attempt in range(2):
                    try:
                        proposed = ask(self.planner, "planner", payload, PlanIR, budget)
                        errors = validate_plan(proposed, q, catalog, rules)
                        if errors:
                            raise InvalidModelOutput(",".join(errors))
                        plan = proposed
                        break
                    except InvalidModelOutput:
                        if attempt:
                            raise
                        payload["validation_feedback"] = (
                            "Previous plan JSON/schema/column/rule validation failed; regenerate from supplied sources."
                        )
                assert plan is not None
                path, reasons = route(plan, time_result)
                trace.update(route=path, route_reasons=reasons)
                candidates = []
                total = 1 if path == "FAST" else min(3, self.config.limits.max_candidates - 1)
                strategies = ["direct", "ctes", "preaggregate"]
                index = 0
                total = max(1, total)
                while index < total:
                    budget.candidate()
                    candidate = ask(
                        self.reasoner,
                        "sql_coder",
                        {
                            **original,
                            "validated_plan": plan.model_dump(),
                            "strategy": strategies[index],
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
                                },
                                SqlCandidate,
                                budget,
                            )
                    index += 1
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
                        total = max(total, min(3, self.config.limits.max_candidates - 1))
                disagreement = False
                if path == "SLOW" and budget.candidates < self.config.limits.max_candidates:
                    budget.candidate()
                    dsl = ask(self.planner, "dsl_coder", original, DataOpsCandidate, budget)
                    dsl_result = execute(
                        dsl, catalog, self.config.limits, budget.remaining_seconds()
                    )
                    trace["independent_dsl"] = {
                        "program_hash": dsl_result.program_hash,
                        "status": "ok",
                    }
                    comparisons = [
                        candidate.answer_type == dsl.answer_type
                        and canonicalize(answer_value(result, candidate.answer_type))
                        == canonicalize(answer_value(dsl_result, dsl.answer_type))
                        for candidate, result, report in candidates
                        if not report.hard_failures
                    ]
                    disagreement = any(not matches for matches in comparisons)
                    trace["cross_paradigm_agreement"] = all(comparisons) if comparisons else None
                failures = sorted({f for _, _, report in candidates for f in report.hard_failures})
                if disagreement:
                    failures.append("cross_paradigm_disagreement")
                decision = abstain(
                    q.id, *(failures or ["uncalibrated_policy", "semantic_verification_incomplete"])
                )
        except (DatamateError, ValueError, OSError) as exc:
            logger.warning(
                "query_fallback", extra={"question_id": q.id, "error_type": type(exc).__name__}
            )
            decision = abstain(q.id, type(exc).__name__)
        if budget.remaining_seconds() <= 0:
            decision = abstain(q.id, ResourceExceeded.__name__)
        trace.update(
            decision=decision.status,
            decision_reason=decision.reason_codes,
            latency_ms=(time.monotonic() - started) * 1000,
            model_calls=budget.model_calls,
        )
        self.last_trace = trace
        trace_dir = self.config.output_root / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_name = hashlib.sha256(q.id.encode()).hexdigest() + ".json"
        (trace_dir / trace_name).write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")
        return decision

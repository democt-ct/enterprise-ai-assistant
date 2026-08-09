"""协作策略回归评测：验证策略与权限边界，不替代模型效果评测。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.collaboration.case_manager import CaseManager
from src.workflow.request_classifier import classify_request


@dataclass(frozen=True)
class CollaborationEvalResult:
    case_id: str
    passed: bool
    actual_mode: str
    actual_participants: list[str]
    failures: list[str]


def evaluate_collaboration_case(case: dict[str, Any]) -> CollaborationEvalResult:
    route = case["route"]
    decision = classify_request(case["query"], route)
    failures: list[str] = []
    if decision.mode.value != case["expected_mode"]:
        failures.append(f"mode={decision.mode.value}")
    participants: list[str] = []
    if decision.mode.value == "collaboration":
        try:
            plan = CaseManager().plan(route, requires_confirmation=decision.requires_confirmation)
            participants = list(plan.participants)
            if participants != case.get("expected_participants", participants):
                failures.append(f"participants={participants}")
            if case.get("expect_read_only") and not plan.read_only:
                failures.append("plan_not_read_only")
        except ValueError:
            if not case.get("expect_manager_rejection"):
                failures.append("manager_rejected")
    return CollaborationEvalResult(
        case_id=case["id"], passed=not failures, actual_mode=decision.mode.value,
        actual_participants=participants, failures=failures,
    )

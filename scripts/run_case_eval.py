#!/usr/bin/env python
"""Case 生命周期与安全边界评测（纯内存数据库，不触碰本地业务数据）。"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.collaboration.case_service import (
    approve_and_execute_mock_proposal, confirm_proposal, create_case, prepare_onboarding,
)
from src.collaboration.case_templates import get_template
from src.collaboration.contracts import ExpertArtifact, ShareScope
from src.collaboration.contracts import AccessMode
from src.collaboration.registry import resource_access_allowed
from src.collaboration.workspace import TaskWorkspace


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE users (user_id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO users VALUES ('EMP001')")
    root = Path("src/tools/db/migrations")
    for name in ("003_case_collaboration.sql", "004_case_proposal_task.sql"):
        conn.executescript((root / name).read_text(encoding="utf-8"))
    return conn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", help="可选 JSON 报告路径")
    args = parser.parse_args()
    conn = _db()
    checks: dict[str, bool] = {}
    try:
        with patch("src.collaboration.case_service.get_db", return_value=conn):
            template = get_template("employee_onboarding")
            case = create_case(owner_user_id="EMP001", template_id=template.template_id, title=template.title, goal="入职协同评测", tasks=list(template.task_specs))
            prepared = prepare_onboarding(case["id"], "EMP001", {"start_date": "2026-09-01", "position": "后端开发工程师"})
            checks["three_proposals_created"] = len(prepared["operation_proposals"]) == 3
            try:
                approve_and_execute_mock_proposal(case["id"], prepared["operation_proposals"][0]["id"], "EMP003")
                checks["approval_required_before_execution"] = False
            except ValueError:
                checks["approval_required_before_execution"] = True
            final = prepared
            for proposal in prepared["operation_proposals"]:
                confirm_proposal(case["id"], proposal["id"], "EMP001")
                final = approve_and_execute_mock_proposal(case["id"], proposal["id"], "EMP003")
            checks["all_tasks_completed"] = all(task["status"] == "completed" for task in final["tasks"])
            checks["case_auto_completed"] = final["status"] == "completed"
        workspace = TaskWorkspace(case_id="privacy", goal="privacy", participants=("legal_agent", "finance_agent"))
        workspace.add_artifact(ExpertArtifact(expert_id="legal_agent", summary="private", share_scope=ShareScope.PRIVATE))
        checks["private_artifact_not_shared"] = workspace.context_for("finance_agent")["prior_artifacts"] == []
        checks["expert_cannot_execute_write"] = not resource_access_allowed("it_agent", "itsm_ticket", AccessMode.EXECUTE_WRITE)
        checks["cross_domain_resource_denied"] = not resource_access_allowed("hr_agent", "itsm_ticket", AccessMode.PROPOSE_WRITE)
    finally:
        conn.close()
    report = {"suite": "case_lifecycle_and_safety", "passed": sum(checks.values()), "total": len(checks), "checks": checks}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    raise SystemExit(0 if report["passed"] == report["total"] else 1)


if __name__ == "__main__":
    main()

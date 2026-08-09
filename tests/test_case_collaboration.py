from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from src.collaboration.case_service import (
    CASE_TRANSITIONS,
    TASK_TRANSITIONS,
    approve_and_execute_mock_proposal,
    confirm_proposal,
    create_case,
    prepare_onboarding,
)
from src.collaboration.case_templates import get_template, list_templates


class CaseTemplateTests(unittest.TestCase):
    def test_onboarding_template_expresses_dependencies_not_agent_instances(self):
        template = get_template("employee_onboarding")

        self.assertIsNotNone(template)
        self.assertEqual("hr", template.task_specs[0].domain)
        self.assertEqual(("hr_profile",), template.task_specs[1].depends_on)
        self.assertFalse(hasattr(template, "agents"))
        self.assertEqual({"hr", "it", "legal", "finance"}, {task.domain for task in template.task_specs})

    def test_cross_domain_consultation_has_no_fixed_task_graph(self):
        template = get_template("cross_domain_consultation")

        self.assertIsNotNone(template)
        self.assertEqual((), template.task_specs)
        self.assertIn(template, list_templates())


class CaseLifecycleTests(unittest.TestCase):
    def test_terminal_case_states_are_not_reopenable(self):
        self.assertEqual(set(), CASE_TRANSITIONS["completed"])
        self.assertEqual(set(), CASE_TRANSITIONS["cancelled"])

    def test_task_cannot_complete_before_it_starts(self):
        self.assertNotIn("completed", TASK_TRANSITIONS["not_started"])
        self.assertIn("completed", TASK_TRANSITIONS["in_progress"])


class CaseMigrationTests(unittest.TestCase):
    def test_case_schema_migration_creates_collaboration_tables(self):
        migration = Path("src/tools/db/migrations/003_case_collaboration.sql")
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(migration.read_text(encoding="utf-8"))
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            conn.close()

        self.assertTrue({
            "cases", "case_tasks", "case_artifacts",
            "case_operation_proposals", "case_events",
        }.issubset(tables))

    def test_proposal_task_migration_links_operation_to_case_task(self):
        conn = sqlite3.connect(":memory:")
        try:
            migration_dir = Path("src/tools/db/migrations")
            conn.executescript((migration_dir / "003_case_collaboration.sql").read_text(encoding="utf-8"))
            conn.executescript((migration_dir / "004_case_proposal_task.sql").read_text(encoding="utf-8"))
            columns = {row[1] for row in conn.execute("PRAGMA table_info(case_operation_proposals)")}
        finally:
            conn.close()

        self.assertIn("task_id", columns)


class OnboardingFlowTests(unittest.TestCase):
    def test_onboarding_moves_from_profile_to_confirmed_mock_operations(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        migration_dir = Path("src/tools/db/migrations")
        conn.execute("CREATE TABLE users (user_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO users (user_id) VALUES ('EMP001')")
        conn.executescript((migration_dir / "003_case_collaboration.sql").read_text(encoding="utf-8"))
        conn.executescript((migration_dir / "004_case_proposal_task.sql").read_text(encoding="utf-8"))
        template = get_template("employee_onboarding")

        try:
            with patch("src.collaboration.case_service.get_db", return_value=conn):
                case = create_case(
                    owner_user_id="EMP001", template_id=template.template_id,
                    title=template.title, goal="为新员工办理入职",
                    tasks=list(template.task_specs),
                )
                prepared = prepare_onboarding(case["id"], "EMP001", {
                    "start_date": "2026-09-01", "position": "后端开发工程师", "department": "工程部",
                })
                self.assertEqual("coordinating", prepared["status"])
                self.assertEqual(3, len(prepared["operation_proposals"]))
                self.assertEqual("completed", next(t["status"] for t in prepared["tasks"] if t["task_key"] == "hr_profile"))

                for proposal in prepared["operation_proposals"]:
                    confirmed = confirm_proposal(case["id"], proposal["id"], "EMP001")
                    self.assertIn("pending_approval", {p["status"] for p in confirmed["operation_proposals"]})
                    final = approve_and_execute_mock_proposal(case["id"], proposal["id"], "EMP003")
        finally:
            conn.close()

        self.assertEqual("completed", final["status"])
        self.assertTrue(all(task["status"] == "completed" for task in final["tasks"]))
        self.assertTrue(all(proposal["status"] == "executed" for proposal in final["operation_proposals"]))


if __name__ == "__main__":
    unittest.main()

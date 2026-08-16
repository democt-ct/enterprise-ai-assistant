from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.collaboration.runtime import CaseRuntime
from src.collaboration.case_manager import CollaborationMode


class CaseRuntimeTests(unittest.TestCase):
    def test_runtime_falls_back_to_temporary_workspace_without_session(self):
        active = CaseRuntime().start(
            fallback_case_id="session-1", goal="检查设备和报销",
            route_result={"primary": "it_agent", "secondary": "finance_agent"},
            requires_confirmation=False,
        )

        self.assertFalse(active.persistent)
        self.assertEqual("session-1", active.workspace.case_id)
        self.assertEqual(("it_agent", "finance_agent"), active.plan.participants)

    def test_runtime_persists_registered_task_graph_for_authenticated_user(self):
        session = SimpleNamespace(user_id="EMP001", role="employee", department_name="工程部")
        with patch("src.collaboration.runtime.create_case", return_value={"id": "case-1"}) as create, \
             patch("src.collaboration.runtime.transition_case"):
            active = CaseRuntime().start(
                fallback_case_id="session-1", goal="检查设备和报销",
                route_result={"primary": "it_agent", "secondary": "finance_agent"},
                requires_confirmation=False, session_ctx=session,
            )

        self.assertTrue(active.persistent)
        self.assertEqual("case-1", active.workspace.case_id)
        self.assertEqual(2, len(create.call_args.kwargs["tasks"]))

    def test_runtime_marks_persistent_case_blocked_on_failure(self):
        active = SimpleNamespace(
            persistent=True,
            workspace=SimpleNamespace(case_id="case-1", audit_events=[]),
        )
        runtime = CaseRuntime()
        with patch("src.collaboration.runtime.transition_case") as transition:
            runtime.mark_blocked(active, "EMP001", "expert_execution_failed")

        transition.assert_called_once_with("case-1", "blocked", "EMP001")
        self.assertEqual("case_blocked", active.workspace.audit_events[0]["event"])

    def test_runtime_plans_handoff_mode_for_single_expert_with_trigger(self):
        runtime = CaseRuntime()
        active = runtime.start(
            fallback_case_id="session-h", goal="补充 IT 故障信息",
            route_result={"primary": "it_agent", "secondary": None, "handoff_trigger": True},
            requires_confirmation=True,
        )
        self.assertEqual(CollaborationMode.HANDOFF, active.plan.mode)
        self.assertTrue(active.plan.tasks[0].key.startswith("handoff_"))
        # trace 视图把 mode 透传给前端调用方
        self.assertEqual("handoff", runtime.trace_view(active)["mode"])


if __name__ == "__main__":
    unittest.main()

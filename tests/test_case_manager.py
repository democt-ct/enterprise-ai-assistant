from __future__ import annotations

import unittest

from src.collaboration.case_manager import CaseManager, CollaborationMode
from src.collaboration.contracts import AccessMode
from src.collaboration.registry import resource_access_allowed


class CaseManagerTests(unittest.TestCase):
    def test_cross_domain_route_generates_parallel_tasks_from_registered_experts(self):
        plan = CaseManager().plan({"primary": "it_agent", "secondary": "finance_agent"})

        self.assertEqual(CollaborationMode.PARALLEL_RESEARCH, plan.mode)
        self.assertEqual(("it_agent", "finance_agent"), plan.participants)
        self.assertEqual({"it", "finance"}, {task.domain for task in plan.tasks})
        self.assertTrue(plan.read_only)

    def test_unknown_agent_cannot_enter_task_graph(self):
        with self.assertRaises(ValueError):
            CaseManager().plan({"primary": "unknown_agent", "secondary": None})

    def test_single_expert_with_handoff_trigger_uses_handoff_mode(self):
        plan = CaseManager().plan(
            {"primary": "it_agent", "secondary": None, "handoff_trigger": True},
            requires_confirmation=True,
        )
        self.assertEqual(CollaborationMode.HANDOFF, plan.mode)
        self.assertEqual(("it_agent",), plan.participants)
        self.assertTrue(plan.tasks[0].key.startswith("handoff_"))

    def test_single_expert_without_handoff_trigger_stays_single_expert(self):
        plan = CaseManager().plan({"primary": "it_agent", "secondary": None})
        self.assertEqual(CollaborationMode.SINGLE_EXPERT, plan.mode)

    def test_expert_can_propose_but_never_execute_registered_write(self):
        self.assertTrue(resource_access_allowed("it_agent", "itsm_ticket", AccessMode.PROPOSE_WRITE))
        self.assertFalse(resource_access_allowed("it_agent", "itsm_ticket", AccessMode.EXECUTE_WRITE))
        self.assertFalse(resource_access_allowed("hr_agent", "itsm_ticket", AccessMode.PROPOSE_WRITE))


if __name__ == "__main__":
    unittest.main()

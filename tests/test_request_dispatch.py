from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.agents.planner import SubTask
from src.api.service.chat_service import chat
from src.collaboration.contracts import ExpertArtifact, ShareScope
from src.collaboration.workspace import TaskWorkspace
from src.workflow.engine import WorkflowEngine
from src.workflow.request_classifier import ExecutionMode, classify_request


class StubOrchestrator:
    def __init__(self, route_result: dict) -> None:
        self.route_result = route_result

    def route(self, query: str, history=None) -> dict:
        return dict(self.route_result)


class StubAgent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.client = None
        self.tools = []
        self.requests = []

    def run(self, request, on_step=None):
        self.requests.append(request)
        return SimpleNamespace(
            answer=f"{self.name} read-only answer",
            agent_name=self.name,
            retrieved_chunks=[],
            tool_calls=[],
            reasoning=None,
            handoff_suggestions=None,
            tokens_used=0,
        )


class RequestClassifierTests(unittest.TestCase):
    def test_analysis_question_is_knowledge_even_when_old_intent_is_action(self):
        decision = classify_request(
            "报销流程是什么？",
            {
                "primary": "finance_agent",
                "secondary": None,
                "method": "keyword",
                "intent": "action",
                "action_card": None,
            },
        )

        self.assertEqual(ExecutionMode.KNOWLEDGE, decision.mode)
        self.assertFalse(decision.requires_confirmation)

    def test_registered_single_domain_action_uses_workflow(self):
        decision = classify_request(
            "帮我提交报销",
            {
                "primary": "finance_agent",
                "secondary": None,
                "method": "keyword",
                "action_card": {"type": "expense_form"},
            },
        )

        self.assertEqual(ExecutionMode.WORKFLOW, decision.mode)
        self.assertTrue(decision.requires_confirmation)

    def test_cross_domain_request_wins_over_single_workflow(self):
        decision = classify_request(
            "申请请假，同时帮我报修电脑",
            {
                "primary": "hr_agent",
                "secondary": "it_agent",
                "method": "keyword",
                "action_card": {"type": "leave_form"},
            },
        )

        self.assertEqual(ExecutionMode.COLLABORATION, decision.mode)
        self.assertTrue(decision.requires_confirmation)


class ChatDispatchTests(unittest.TestCase):
    def test_registered_workflow_does_not_call_agent(self):
        route = {
            "primary": "finance_agent",
            "secondary": None,
            "confidence": 0.9,
            "method": "keyword",
            "intent": "action",
            "action_card": {"type": "expense_form"},
        }
        agent = StubAgent("finance_agent")

        result = chat(
            "帮我提交报销",
            StubOrchestrator(route),
            {"finance_agent": agent},
            session_id="workflow-test",
        )

        self.assertEqual("workflow", result["routing"]["execution_mode"])
        self.assertEqual("业务工作流", result["agent_name"])
        self.assertEqual([], result["tool_calls"])
        self.assertEqual([], agent.requests)

    def test_knowledge_question_forces_query_mode(self):
        route = {
            "primary": "finance_agent",
            "secondary": None,
            "confidence": 0.9,
            "method": "keyword",
            "intent": "action",
            "action_card": None,
        }
        agent = StubAgent("finance_agent")

        result = chat(
            "报销流程是什么？",
            StubOrchestrator(route),
            {"finance_agent": agent},
            session_id="knowledge-test",
        )

        self.assertEqual("knowledge", result["routing"]["execution_mode"])
        self.assertEqual("query", agent.requests[0].intent)

    def test_cross_domain_write_request_is_read_only(self):
        route = {
            "primary": "hr_agent",
            "secondary": "it_agent",
            "confidence": 0.8,
            "method": "keyword",
            "intent": "action",
            "action_card": {"type": "leave_form"},
        }
        hr_agent = StubAgent("hr_agent")
        it_agent = StubAgent("it_agent")

        result = chat(
            "申请请假，同时帮我报修电脑",
            StubOrchestrator(route),
            {"hr_agent": hr_agent, "it_agent": it_agent},
            session_id="collaboration-test",
        )

        self.assertEqual("collaboration", result["routing"]["execution_mode"])
        self.assertTrue(result["routing"]["requires_confirmation"])
        self.assertEqual("query", hr_agent.requests[0].intent)
        self.assertEqual("query", it_agent.requests[0].intent)
        self.assertEqual([], result["tool_calls"])
        self.assertEqual("collaboration-test", result["collaboration"]["case_id"])
        self.assertEqual(2, result["collaboration"]["artifact_count"])

    def test_authenticated_cross_domain_chat_persists_a_case_and_artifacts(self):
        route = {
            "primary": "hr_agent", "secondary": "it_agent", "confidence": 0.8,
            "method": "keyword", "intent": "query", "action_card": None,
        }
        session = SimpleNamespace(
            user_id="EMP001", name="张三", role="employee", department_name="工程部",
        )
        with patch("src.collaboration.runtime.create_case", return_value={"id": "case-001"}) as create, \
             patch("src.collaboration.runtime.transition_case") as transition, \
             patch("src.collaboration.runtime.get_case", return_value={"tasks": [
                 {"id": "task-hr", "domain": "hr"}, {"id": "task-it", "domain": "it"},
             ]}), \
             patch("src.collaboration.runtime.transition_task"), \
             patch("src.collaboration.runtime.record_artifact") as record:
            result = chat(
                "请假期间电脑损坏了，分别有什么安排？",
                StubOrchestrator(route),
                {"hr_agent": StubAgent("hr_agent"), "it_agent": StubAgent("it_agent")},
                session_id="session-001",
                session_ctx=session,
            )

        self.assertEqual("case-001", result["collaboration"]["case_id"])
        self.assertEqual(1, create.call_count)
        self.assertEqual(2, record.call_count)
        self.assertEqual(2, transition.call_count)


class WorkflowEngineSafetyTests(unittest.TestCase):
    def test_read_only_planner_downgrades_tool_node_to_query_agent(self):
        agent = StubAgent("hr_agent")
        engine = WorkflowEngine(
            {"hr_agent": agent},
            tool_agent=SimpleNamespace(),
        )
        task = SubTask(
            id="t1",
            query="查询年假余额",
            domain="hr",
            agent="tool",
        )

        result = engine._execute_node(task, None, [], read_only=True)

        self.assertNotIn("error", result)
        self.assertEqual("query", agent.requests[0].intent)


class WorkspacePrivacyTests(unittest.TestCase):
    def test_private_artifact_is_not_shared_with_other_expert(self):
        workspace = TaskWorkspace(
            case_id="case-1",
            goal="检查合同和预算",
            participants=("legal_agent", "finance_agent"),
        )
        workspace.add_artifact(
            ExpertArtifact(
                expert_id="legal_agent",
                summary="合同原文中的保密条款",
                share_scope=ShareScope.PRIVATE,
            )
        )

        context = workspace.context_for("finance_agent")

        self.assertEqual([], context["prior_artifacts"])


if __name__ == "__main__":
    unittest.main()

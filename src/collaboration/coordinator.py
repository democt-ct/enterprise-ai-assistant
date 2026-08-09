"""将现有专家执行包装为可治理的协作案例。"""

from __future__ import annotations

from typing import Any

from .contracts import ExpertArtifact, ShareScope
from .registry import get_expert_profile
from .workspace import TaskWorkspace


class CollaborationCoordinator:
    """负责创建工作区、最小化上下文和归档专家产物。

    它不执行领域推理，也不直接调用外部系统；这些职责仍属于专家 Pod 和
    审批网关。当前版本将既有 BaseAgent 的响应规范化为 Artifact，为后续
    资源适配器与任务图替换提供稳定边界。
    """

    def open_case(
        self,
        case_id: str,
        goal: str,
        participants: list[str],
        session_ctx: Any = None,
    ) -> TaskWorkspace:
        profiles = [get_expert_profile(expert_id) for expert_id in participants]
        shared_context: dict[str, Any] = {}
        if session_ctx:
            # 只共享任务需要的组织属性，不把用户姓名、完整会话或身份凭据
            # 广播给所有专家。
            shared_context = {
                "department": getattr(session_ctx, "department_name", None),
                "role": getattr(session_ctx, "role", None),
            }
            shared_context = {key: value for key, value in shared_context.items() if value}

        workspace = TaskWorkspace(
            case_id=case_id,
            goal=goal,
            participants=tuple(profile.expert_id for profile in profiles),
            shared_context=shared_context,
        )
        workspace.audit_events.append({
            "event": "case_opened",
            "participants": list(workspace.participants),
        })
        return workspace

    def expert_context(self, workspace: TaskWorkspace, expert_id: str) -> dict[str, Any]:
        profile = get_expert_profile(expert_id)
        context = workspace.context_for(expert_id)
        context["expert_profile"] = {
            "display_name": profile.display_name,
            "domain": profile.domain,
            "private_resources": list(profile.private_resources),
            "shared_services": list(profile.shared_services),
        }
        return context

    def record_response(self, workspace: TaskWorkspace, expert_id: str, response: dict[str, Any]) -> ExpertArtifact:
        """把旧 Agent 响应转换为共享协作产物。"""
        chunks = response.get("retrieved_chunks", [])
        evidence_refs = []
        for chunk in chunks[:3]:
            if not isinstance(chunk, dict):
                continue
            metadata = chunk.get("metadata") or {}
            evidence_refs.append(str(chunk.get("source") or metadata.get("source") or "knowledge_base"))
        artifact = ExpertArtifact(
            expert_id=expert_id,
            summary=response.get("answer", ""),
            facts=[],
            evidence_refs=evidence_refs,
            recommendations=[response.get("answer", "")] if response.get("answer") else [],
            risk_flags=[],
            confidence=0.85 if evidence_refs else 0.55,
            next_actions=list(response.get("handoff_suggestions") or []),
            share_scope=ShareScope.TEAM,
        )
        workspace.add_artifact(artifact)
        return artifact

    @staticmethod
    def composer_inputs(workspace: TaskWorkspace) -> list[dict[str, str]]:
        """整合器只消费 Artifact 摘要，不消费完整私有执行上下文。"""
        return [
            {"agent_name": get_expert_profile(artifact.expert_id).display_name, "answer": artifact.summary}
            for artifact in workspace.artifacts
            if artifact.share_scope is not ShareScope.PRIVATE
        ]

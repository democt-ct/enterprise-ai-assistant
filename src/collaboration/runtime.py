"""Case 协作运行时：统一任务规划、工作区与持久化状态。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .case_manager import CaseManager, CasePlan
from .case_service import create_case, get_case, record_artifact, transition_case, transition_task
from .case_templates import get_template
from .coordinator import CollaborationCoordinator
from .registry import get_expert_profile
from .workspace import TaskWorkspace

logger = logging.getLogger(__name__)


@dataclass
class ActiveCase:
    plan: CasePlan
    workspace: TaskWorkspace
    persistent: bool


class CaseRuntime:
    """协作控制平面；聊天/API 不再手工同步内存工作区和数据库。"""

    def __init__(self) -> None:
        self.manager = CaseManager()
        self.coordinator = CollaborationCoordinator()

    def start(self, *, fallback_case_id: str, goal: str, route_result: dict[str, Any], requires_confirmation: bool, session_ctx: Any = None) -> ActiveCase:
        plan = self.manager.plan(route_result, requires_confirmation=requires_confirmation)
        case_id = fallback_case_id
        persistent = False
        if session_ctx:
            try:
                template = get_template("cross_domain_consultation")
                stored = create_case(
                    owner_user_id=session_ctx.user_id,
                    template_id=template.template_id,
                    title=template.title,
                    goal=goal,
                    tasks=list(plan.tasks),
                    context={key: value for key, value in {
                        "department": getattr(session_ctx, "department_name", None),
                        "role": getattr(session_ctx, "role", None),
                        "collaboration_mode": plan.mode.value,
                    }.items() if value},
                )
                case_id = stored["id"]
                transition_case(case_id, "coordinating", session_ctx.user_id)
                persistent = True
            except Exception:
                logger.exception("无法创建持久化协作 Case，回退为临时工作区")
        workspace = self.coordinator.open_case(case_id, goal, list(plan.participants), session_ctx)
        return ActiveCase(plan=plan, workspace=workspace, persistent=persistent)

    def expert_context(self, active: ActiveCase, expert_id: str) -> dict[str, Any]:
        return self.coordinator.expert_context(active.workspace, expert_id)

    def record_response(self, active: ActiveCase, expert_id: str, response: dict[str, Any]):
        return self.coordinator.record_response(active.workspace, expert_id, response)

    def finalize(self, active: ActiveCase, actor_id: str) -> None:
        if not active.persistent:
            return
        case_id = active.workspace.case_id
        try:
            tasks = (get_case(case_id) or {}).get("tasks", [])
            task_by_domain = {task["domain"]: task["id"] for task in tasks}
            for artifact in active.workspace.artifacts:
                task_id = task_by_domain.get(get_expert_profile(artifact.expert_id).domain)
                record_artifact(case_id, artifact, task_id=task_id)
                if task_id:
                    transition_task(case_id, task_id, "completed", "case_manager")
            transition_case(case_id, "completed", actor_id)
        except Exception:
            logger.exception("无法提交协作 Case 结果")

    def mark_blocked(self, active: ActiveCase, actor_id: str, reason: str) -> None:
        """专家执行失败时保留 Case，并进入可恢复的阻塞态。"""
        active.workspace.audit_events.append({"event": "case_blocked", "reason": reason})
        if not active.persistent:
            return
        try:
            transition_case(active.workspace.case_id, "blocked", actor_id)
        except Exception:
            logger.exception("无法将协作 Case 标记为 blocked")

    def composer_inputs(self, active: ActiveCase) -> list[dict[str, str]]:
        return self.coordinator.composer_inputs(active.workspace)

    @staticmethod
    def trace_view(active: ActiveCase) -> dict[str, Any]:
        return {
            **active.workspace.trace_view(),
            "mode": active.plan.mode.value,
            "rationale": active.plan.rationale,
            "read_only": active.plan.read_only,
            "persistent": active.persistent,
        }

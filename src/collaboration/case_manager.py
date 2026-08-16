"""受能力注册表约束的动态 Case Manager。

它把路由结果转换为任务图，而不是让模型直接决定任意工具调用或数据库写入。
当前策略确定性、可测试；后续可由 LLM 提出候选计划，但必须通过同一校验器。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .case_service import CaseTaskSpec
from .registry import EXPERT_REGISTRY


class CollaborationMode(str, Enum):
    SINGLE_EXPERT = "single_expert"
    PARALLEL_RESEARCH = "parallel_research"
    HANDOFF = "handoff"
    CONTROLLED_WORKFLOW = "controlled_workflow"


@dataclass(frozen=True)
class CasePlan:
    mode: CollaborationMode
    participants: tuple[str, ...]
    tasks: tuple[CaseTaskSpec, ...]
    rationale: str
    read_only: bool


class CaseManager:
    """从已路由的候选专家生成最小任务图。"""

    def plan(self, route_result: dict[str, Any], *, requires_confirmation: bool = False) -> CasePlan:
        primary = route_result.get("primary")
        secondary = route_result.get("secondary")
        participants = tuple(
            agent for agent in (primary, secondary)
            if agent in EXPERT_REGISTRY
        )
        # 去重后保持路由优先级；未知专家不能进入任务图。
        participants = tuple(dict.fromkeys(participants))
        if not participants:
            raise ValueError("没有已注册的专家可以参与该 Case")

        # HANDOFF：单领域但需要多轮补充资料（如流转给某位专家采集缺失字段、
        # 保持 Case 上下文后再回到整合）。这是枚举里已定义但此前从未被
        # plan() 生成的协作模式。
        handoff_trigger = route_result.get("handoff_trigger") or (
            requires_confirmation and len(participants) == 1
        )
        if len(participants) == 1 and handoff_trigger:
            agent = participants[0]
            return CasePlan(
                mode=CollaborationMode.HANDOFF,
                participants=participants,
                tasks=(CaseTaskSpec(
                    f"handoff_{agent}",
                    f"{EXPERT_REGISTRY[agent].display_name}补充资料",
                    EXPERT_REGISTRY[agent].domain,
                ),),
                rationale="请求只涉及一个领域但需要向该专家补充资料；进入可恢复的对话交接。",
                read_only=not requires_confirmation,
            )

        if len(participants) == 1:
            agent = participants[0]
            return CasePlan(
                mode=CollaborationMode.SINGLE_EXPERT,
                participants=participants,
                tasks=(CaseTaskSpec(f"research_{agent}", f"{EXPERT_REGISTRY[agent].display_name}领域研判", EXPERT_REGISTRY[agent].domain),),
                rationale="请求只涉及一个已注册领域，使用单专家证据化研判。",
                read_only=not requires_confirmation,
            )

        tasks = tuple(
            CaseTaskSpec(
                key=f"research_{agent}",
                title=f"{EXPERT_REGISTRY[agent].display_name}领域研判",
                domain=EXPERT_REGISTRY[agent].domain,
            )
            for agent in participants
        )
        return CasePlan(
            mode=CollaborationMode.PARALLEL_RESEARCH,
            participants=participants,
            tasks=tasks,
            rationale="请求跨越多个已注册领域，专家并行生成可共享 Artifact 后统一整合。",
            read_only=True,
        )

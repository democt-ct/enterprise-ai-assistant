"""请求执行模式分类。

领域路由回答“由谁处理”，本模块只回答“用哪种方式处理”。两者拆开后，
知识问答不会因为包含“怎么、流程”等词误入工具调用，确定性业务也不会
交给 Agent 自由决定是否执行写操作。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ExecutionMode(str, Enum):
    """聊天入口支持的四种执行模式。"""

    FALLBACK = "fallback"
    KNOWLEDGE = "knowledge"
    WORKFLOW = "workflow"
    COLLABORATION = "collaboration"


_WRITE_MARKERS = (
    "申请",
    "提交",
    "创建",
    "发起",
    "审批",
    "通过",
    "驳回",
    "撤回",
    "取消",
    "删除",
    "修改",
    "更新",
    "重置",
    "安装",
    "卸载",
    "开通",
    "关闭",
    "打卡",
    "签到",
    "签退",
    "报修",
)


@dataclass(frozen=True)
class RequestDecision:
    """执行模式以及选择它的可观测原因。"""

    mode: ExecutionMode
    reason: str
    requires_confirmation: bool = False
    complexity_score: float = 0.0

    def routing_fields(self) -> dict[str, Any]:
        return {
            "execution_mode": self.mode.value,
            "execution_reason": self.reason,
            "requires_confirmation": self.requires_confirmation,
            "complexity_score": self.complexity_score,
        }


def complexity_score(query: str, route_result: dict[str, Any]) -> float:
    """对"用哪种执行方式"做可观测的复杂度打分（0~1）。

    分数越高越倾向协作/工作流，低分倾向只读知识问答。这只是确定性启发式，
    不是训练模型；它让"按请求复杂度选路径"有真实的、可测试的量化依据。
    """
    primary = route_result.get("primary", "fallback")
    secondary = route_result.get("secondary")
    method = route_result.get("method", "unknown")

    score = 0.0
    signals: list[str] = []

    if secondary and secondary != primary:
        score += 0.35
        signals.append("multi_domain")
    elif method in {"context", "llm"} or primary not in ("fallback",):
        score += 0.10
        signals.append("extended_signal")

    if _contains_write_intent(query):
        score += 0.30
        signals.append("write_intent")

    if _contains_complexity_markers(query):
        score += 0.20
        signals.append("analysis_or_missing_fields")

    if route_result.get("action_card"):
        score += 0.15
        signals.append("registered_workflow")

    clipped = min(1.0, round(score, 2))
    return clipped


def _contains_complexity_markers(query: str) -> bool:
    """结构化分析 / 可能缺失字段的表达，贡献一部分复杂度。"""
    markers = (
        "区别", "对比", "比较", "哪种", "差异", "如何", "怎么办", "需要准备什么",
        "流程", "步骤", "条件", "要求", "规定", "计算",
    )
    return any(marker in query for marker in markers)


def classify_request(query: str, route_result: dict[str, Any]) -> RequestDecision:
    """根据领域路由结果选择执行模式。

    优先级是：兜底 → 跨领域协作 → 已注册工作流 → 知识问答。
    跨领域优先于工作流，避免"请假并报修"被单个请假流程提前吞掉。
    每个分支都会附带一个可观测的复杂度打分（complexity_score），把"选哪条
    路径"从隐式优先级变成可解释、可断言的量化决策。
    """

    primary = route_result.get("primary", "fallback")
    secondary = route_result.get("secondary")
    method = route_result.get("method", "unknown")
    action_card = route_result.get("action_card")

    if primary == "fallback" or method == "greeting":
        return RequestDecision(ExecutionMode.FALLBACK, "greeting_or_out_of_scope", complexity_score=0.0)

    score = complexity_score(query, route_result)
    has_write_intent = _contains_write_intent(query)
    if secondary and secondary != primary:
        needs_confirmation = bool(action_card) or has_write_intent
        reason = "cross_domain_write_request" if needs_confirmation else "cross_domain_request"
        return RequestDecision(
            ExecutionMode.COLLABORATION,
            reason,
            requires_confirmation=needs_confirmation,
            complexity_score=round(min(1.0, score), 2),
        )

    if action_card:
        return RequestDecision(
            ExecutionMode.WORKFLOW,
            f"registered_workflow:{action_card.get('type', 'unknown')}",
            requires_confirmation=True,
            complexity_score=round(min(1.0, score), 2),
        )

    # “流程是什么”“怎么报销”属于知识问答，而不是写操作。只有已经注册
    # 成工作流的动作才允许从聊天入口进入提交链路。
    return RequestDecision(
        ExecutionMode.KNOWLEDGE,
        "single_domain_read_only",
        complexity_score=round(score, 2),
    )


def _contains_write_intent(query: str) -> bool:
    normalized = query.replace(" ", "").lower()
    return any(marker in normalized for marker in _WRITE_MARKERS)

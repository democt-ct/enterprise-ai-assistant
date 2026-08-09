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

    def routing_fields(self) -> dict[str, Any]:
        return {
            "execution_mode": self.mode.value,
            "execution_reason": self.reason,
            "requires_confirmation": self.requires_confirmation,
        }


def classify_request(query: str, route_result: dict[str, Any]) -> RequestDecision:
    """根据领域路由结果选择执行模式。

    优先级是：兜底 → 跨领域协作 → 已注册工作流 → 知识问答。
    跨领域优先于工作流，避免“请假并报修”被单个请假流程提前吞掉。
    """

    primary = route_result.get("primary", "fallback")
    secondary = route_result.get("secondary")
    method = route_result.get("method", "unknown")
    action_card = route_result.get("action_card")

    if primary == "fallback" or method == "greeting":
        return RequestDecision(ExecutionMode.FALLBACK, "greeting_or_out_of_scope")

    has_write_intent = _contains_write_intent(query)
    if secondary and secondary != primary:
        needs_confirmation = bool(action_card) or has_write_intent
        reason = "cross_domain_write_request" if needs_confirmation else "cross_domain_request"
        return RequestDecision(
            ExecutionMode.COLLABORATION,
            reason,
            requires_confirmation=needs_confirmation,
        )

    if action_card:
        return RequestDecision(
            ExecutionMode.WORKFLOW,
            f"registered_workflow:{action_card.get('type', 'unknown')}",
            requires_confirmation=True,
        )

    # “流程是什么”“怎么报销”属于知识问答，而不是写操作。只有已经注册
    # 成工作流的动作才允许从聊天入口进入提交链路。
    return RequestDecision(ExecutionMode.KNOWLEDGE, "single_domain_read_only")


def _contains_write_intent(query: str) -> bool:
    normalized = query.replace(" ", "").lower()
    return any(marker in normalized for marker in _WRITE_MARKERS)

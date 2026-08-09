"""对话式请假信息收集。

这个模块只负责把自然语言逐步变成可提交的请假草稿；它不直接执行
写操作。真正的提交仍然必须由用户在确认卡片中明确点击后完成。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


LEAVE_TYPES = {
    "年假": ("年假", "休年假"),
    "病假": ("病假", "生病"),
    "事假": ("事假",),
}
START_PATTERNS = ("请假", "休假", "休个假", "申请假")
CANCEL_WORDS = ("取消", "算了", "不用了", "不请了")


@dataclass
class LeaveDraft:
    leave_type: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    reason: str = ""
    requested_days: int | None = None
    asked_fields: set[str] = field(default_factory=set)


class LeaveDialogue:
    """按会话保存的轻量请假草稿状态机。"""

    def __init__(self) -> None:
        self._drafts: dict[str, LeaveDraft] = {}
        self._lock = threading.RLock()

    def is_relevant(self, session_id: str, query: str) -> bool:
        """当前会话正在请假，或本轮明确发起请假时返回 True。"""
        with self._lock:
            return session_id in self._drafts or self.is_leave_request(query)

    def has_active(self, session_id: str) -> bool:
        """当前会话是否已有待补充的请假草稿。"""
        with self._lock:
            return session_id in self._drafts

    @staticmethod
    def is_leave_request(query: str) -> bool:
        normalized = query.replace(" ", "")
        return (
            any(word in normalized for word in START_PATTERNS)
            or (
                any(verb in normalized for verb in ("请", "休", "申请"))
                and any(leave_type in normalized for leave_type in LEAVE_TYPES)
            )
        )

    def handle(self, session_id: str, query: str) -> dict[str, Any] | None:
        """处理一轮输入；无关输入返回 None，交由正常 Agent 流程处理。"""
        with self._lock:
            active = self._drafts.get(session_id)
            if active is None and not self.is_leave_request(query):
                return None

            if any(word in query for word in CANCEL_WORDS):
                self._drafts.pop(session_id, None)
                return self._result("已取消本次请假申请。之后需要时直接告诉我即可。", "cancelled")

            # 明确发起新请假时，开始一张新草稿，避免把旧信息带入新申请。
            if self.is_leave_request(query) and active is None:
                active = LeaveDraft()
                self._drafts[session_id] = active

            assert active is not None
            self._update_draft(active, query)

            missing = self._missing(active)
            if missing:
                field = missing[0]
                active.asked_fields.add(field)
                return self._result(self._question(active, field), "collecting", active)

            return self._result(
                "请确认以下请假信息。确认后我才会提交申请。",
                "ready_for_confirmation",
                active,
                action_card=self._confirmation_card(active),
            )

    def complete(self, session_id: str) -> None:
        """在确认卡片提交成功后清除草稿。"""
        with self._lock:
            self._drafts.pop(session_id, None)

    def _update_draft(self, draft: LeaveDraft, query: str) -> None:
        for leave_type, keywords in LEAVE_TYPES.items():
            if any(keyword in query for keyword in keywords):
                draft.leave_type = leave_type
                break

        days_match = re.search(r"(?:请|休|共|一共)?\s*(\d+)\s*(?:个)?(?:工作)?天", query)
        if days_match:
            draft.requested_days = int(days_match.group(1))

        start, end = _extract_date_range(query)
        if start:
            draft.start_date = start.isoformat()
        if end:
            draft.end_date = end.isoformat()
        elif start and draft.requested_days:
            draft.end_date = (start + timedelta(days=draft.requested_days - 1)).isoformat()

        reason_match = re.search(r"(?:原因是|原因|因为|因)\s*([^，。；;]+)", query)
        if reason_match:
            draft.reason = reason_match.group(1).strip()

    @staticmethod
    def _missing(draft: LeaveDraft) -> list[str]:
        required = ("leave_type", "start_date", "end_date")
        return [name for name in required if not getattr(draft, name)]

    @staticmethod
    def _question(draft: LeaveDraft, field: str) -> str:
        if field == "leave_type":
            return "好的。你想申请哪一种假期：年假、病假还是事假？"
        if field == "start_date":
            return "请问从哪天开始请假？例如：2026年8月3日。"
        if field == "end_date":
            if draft.requested_days:
                return f"已记录请 {draft.requested_days} 天。请告诉我开始日期，我会为你计算结束日期。"
            return "请问请假到哪天结束？也可以直接告诉我一共请几天。"
        return "请补充请假信息。"

    @staticmethod
    def _confirmation_card(draft: LeaveDraft) -> dict[str, Any]:
        return {
            "type": "leave_confirmation",
            "title": "确认请假申请",
            "leave_type": draft.leave_type,
            "start_date": draft.start_date,
            "end_date": draft.end_date,
            "reason": draft.reason,
            "submit_action": "confirm_leave",
        }

    @staticmethod
    def _result(
        answer: str,
        status: str,
        draft: LeaveDraft | None = None,
        action_card: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "answer": answer,
            "status": status,
            "routing": {
                "primary": "hr_agent",
                "secondary": None,
                "confidence": 1.0,
                "method": "leave_dialogue",
                "execution_mode": "workflow",
                "execution_reason": "active_leave_dialogue",
                "requires_confirmation": True,
            },
            "agent_name": "HR 专家",
            "retrieved_chunks": [],
            "tool_calls": [],
            "reasoning": None,
            "tokens_used": 0,
            "action_card": action_card,
            "draft": {
                "leave_type": draft.leave_type,
                "start_date": draft.start_date,
                "end_date": draft.end_date,
            } if draft else None,
        }


def _extract_date_range(text: str, today: date | None = None) -> tuple[date | None, date | None]:
    """提取常见的中文或 ISO 日期范围，范围右侧可以省略年份/月。"""
    today = today or date.today()
    normalized = text.replace("至", "到").replace("~", "到").replace("－", "-")

    iso_dates = re.findall(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", normalized)
    if iso_dates:
        parsed = [date(int(year), int(month), int(day)) for year, month, day in iso_dates]
        return parsed[0], parsed[1] if len(parsed) > 1 else None

    if "明天" in normalized:
        return today + timedelta(days=1), None
    if "后天" in normalized:
        return today + timedelta(days=2), None

    matches = list(re.finditer(r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})[日号]?", normalized))
    if not matches:
        return None, None

    def build(match: re.Match[str], fallback_year: int, fallback_month: int | None = None) -> date:
        year = int(match.group(1) or fallback_year)
        month = int(match.group(2) or fallback_month or today.month)
        day = int(match.group(3))
        return date(year, month, day)

    start = build(matches[0], today.year)
    if not matches[0].group(1) and start < today:
        start = date(today.year + 1, start.month, start.day)
    if len(matches) == 1:
        return start, None
    end = build(matches[1], start.year, start.month)
    if end < start and not matches[1].group(1):
        end = date(start.year + 1, end.month, end.day)
    return start, end

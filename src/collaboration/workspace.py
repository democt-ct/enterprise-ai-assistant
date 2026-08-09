"""最小共享任务工作区。

工作区保存协作必要的目标、共享上下文与专家 Artifact；它不保存专家私有
提示词、原始检索内容或完整工具响应。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .contracts import ExpertArtifact, OperationProposal


@dataclass
class TaskWorkspace:
    case_id: str
    goal: str
    participants: tuple[str, ...]
    shared_context: dict[str, Any] = field(default_factory=dict)
    artifacts: list[ExpertArtifact] = field(default_factory=list)
    audit_events: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def context_for(self, expert_id: str) -> dict[str, Any]:
        """创建给单个专家的最小共享上下文。"""
        if expert_id not in self.participants:
            raise ValueError(f"{expert_id} is not a participant of case {self.case_id}")
        with self._lock:
            artifacts = [artifact.shared_view() for artifact in self.artifacts]
            return {
                "case_id": self.case_id,
                "goal": self.goal,
                "shared_context": dict(self.shared_context),
                "prior_artifacts": [artifact for artifact in artifacts if artifact is not None],
            }

    def add_artifact(self, artifact: ExpertArtifact) -> None:
        if artifact.expert_id not in self.participants:
            raise ValueError(f"artifact from non-participant: {artifact.expert_id}")
        with self._lock:
            self.artifacts.append(artifact)
            self.audit_events.append({
                "event": "artifact_recorded",
                "expert_id": artifact.expert_id,
                "share_scope": artifact.share_scope.value,
            })

    def operation_proposals(self) -> list[OperationProposal]:
        with self._lock:
            return [
                proposal
                for artifact in self.artifacts
                for proposal in artifact.operation_proposals
            ]

    def trace_view(self) -> dict[str, Any]:
        """可返回给前端的协作摘要，不包含私有内容。"""
        with self._lock:
            return {
                "case_id": self.case_id,
                "participants": list(self.participants),
                "artifact_count": len(self.artifacts),
                "operation_proposal_count": len(self.operation_proposals()),
                "events": list(self.audit_events),
            }

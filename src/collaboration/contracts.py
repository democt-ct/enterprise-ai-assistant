"""企业协作中枢的领域契约。

这些对象用于在协调器、专家 Pod、工作流和外部资源之间传递信息，避免
用一段无结构文本承担事实、建议、风险和写操作意图。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ShareScope(str, Enum):
    PUBLIC = "public"
    TEAM = "team"
    PRIVATE = "private"


class ResourceType(str, Enum):
    KNOWLEDGE = "knowledge"
    CONNECTOR = "connector"
    WORKFLOW = "workflow"
    SHARED_SERVICE = "shared_service"


class AccessMode(str, Enum):
    READ = "read"
    PROPOSE_WRITE = "propose_write"
    EXECUTE_WRITE = "execute_write"


@dataclass(frozen=True)
class ResourceManifest:
    resource_id: str
    resource_type: ResourceType
    owner_domain: str
    access_mode: AccessMode
    required_scopes: tuple[str, ...] = ()
    sensitivity: str = "internal"
    approval_required: bool = False


@dataclass(frozen=True)
class ExpertProfile:
    expert_id: str
    display_name: str
    domain: str
    private_resources: tuple[str, ...]
    shared_services: tuple[str, ...] = ("case_workspace", "trace")


@dataclass
class OperationProposal:
    """专家提出、但尚未执行的外部操作。"""

    resource_id: str
    action: str
    summary: str
    required_approval: bool = True
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExpertArtifact:
    """专家产物：供共享工作区和整合器消费的最小结构化结果。"""

    expert_id: str
    summary: str
    facts: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    operation_proposals: list[OperationProposal] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    confidence: float = 0.5
    next_actions: list[str] = field(default_factory=list)
    share_scope: ShareScope = ShareScope.TEAM

    def shared_view(self) -> dict[str, Any] | None:
        """返回可供其他专家读取的最小视图；私有产物不进入共享上下文。"""
        if self.share_scope is ShareScope.PRIVATE:
            return None
        return {
            "expert_id": self.expert_id,
            "summary": self.summary,
            "facts": self.facts,
            "evidence_refs": self.evidence_refs,
            "recommendations": self.recommendations,
            "risk_flags": self.risk_flags,
            "confidence": self.confidence,
            "next_actions": self.next_actions,
            "operation_proposals": [
                {
                    "resource_id": proposal.resource_id,
                    "action": proposal.action,
                    "summary": proposal.summary,
                    "required_approval": proposal.required_approval,
                }
                for proposal in self.operation_proposals
            ],
        }

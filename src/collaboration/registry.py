"""专家 Pod 与外挂资源的显式注册表。"""

from __future__ import annotations

from .contracts import AccessMode, ExpertProfile, ResourceManifest, ResourceType


RESOURCE_REGISTRY: dict[str, ResourceManifest] = {
    "hr_policy_kb": ResourceManifest("hr_policy_kb", ResourceType.KNOWLEDGE, "hr", AccessMode.READ),
    "it_policy_kb": ResourceManifest("it_policy_kb", ResourceType.KNOWLEDGE, "it", AccessMode.READ),
    "legal_policy_kb": ResourceManifest("legal_policy_kb", ResourceType.KNOWLEDGE, "legal", AccessMode.READ, sensitivity="confidential"),
    "finance_policy_kb": ResourceManifest("finance_policy_kb", ResourceType.KNOWLEDGE, "finance", AccessMode.READ),
    "hris_leave": ResourceManifest("hris_leave", ResourceType.CONNECTOR, "hr", AccessMode.PROPOSE_WRITE, approval_required=True),
    "itsm_ticket": ResourceManifest("itsm_ticket", ResourceType.CONNECTOR, "it", AccessMode.PROPOSE_WRITE, approval_required=True),
    "contract_service": ResourceManifest("contract_service", ResourceType.CONNECTOR, "legal", AccessMode.READ, sensitivity="confidential"),
    "expense_service": ResourceManifest("expense_service", ResourceType.CONNECTOR, "finance", AccessMode.PROPOSE_WRITE, approval_required=True),
    "identity_provisioning": ResourceManifest("identity_provisioning", ResourceType.CONNECTOR, "it", AccessMode.PROPOSE_WRITE, approval_required=True),
    "nda_service": ResourceManifest("nda_service", ResourceType.CONNECTOR, "legal", AccessMode.PROPOSE_WRITE, approval_required=True),
    "payroll_service": ResourceManifest("payroll_service", ResourceType.CONNECTOR, "finance", AccessMode.PROPOSE_WRITE, approval_required=True),
    "approval_gateway": ResourceManifest("approval_gateway", ResourceType.WORKFLOW, "shared", AccessMode.EXECUTE_WRITE, approval_required=True),
    "case_workspace": ResourceManifest("case_workspace", ResourceType.SHARED_SERVICE, "shared", AccessMode.READ),
    "trace": ResourceManifest("trace", ResourceType.SHARED_SERVICE, "shared", AccessMode.READ),
}


EXPERT_REGISTRY: dict[str, ExpertProfile] = {
    "hr_agent": ExpertProfile("hr_agent", "HR 专家", "hr", ("hr_policy_kb", "hris_leave")),
    "it_agent": ExpertProfile("it_agent", "IT 专家", "it", ("it_policy_kb", "itsm_ticket")),
    "legal_agent": ExpertProfile("legal_agent", "法务专家", "legal", ("legal_policy_kb", "contract_service")),
    "finance_agent": ExpertProfile("finance_agent", "财务专家", "finance", ("finance_policy_kb", "expense_service")),
}

# 旧 ToolDef 到新资源模型的显式适配。未登记工具默认拒绝。
TOOL_RESOURCE_BINDINGS: dict[str, tuple[str, str, AccessMode]] = {
    "get_leave_balance": ("hr_agent", "hr_policy_kb", AccessMode.READ),
    "get_org_chain": ("hr_agent", "hr_policy_kb", AccessMode.READ),
    "check_policy": ("hr_agent", "hr_policy_kb", AccessMode.READ),
    "get_approval_progress": ("hr_agent", "hr_policy_kb", AccessMode.READ),
    "get_pending_approvals": ("hr_agent", "hr_policy_kb", AccessMode.READ),
    "submit_leave_request": ("hr_agent", "hris_leave", AccessMode.PROPOSE_WRITE),
    "approve_leave": ("hr_agent", "hris_leave", AccessMode.PROPOSE_WRITE),
    "reject_leave": ("hr_agent", "hris_leave", AccessMode.PROPOSE_WRITE),
    "check_ticket_status": ("it_agent", "it_policy_kb", AccessMode.READ),
    "check_device_inventory": ("it_agent", "it_policy_kb", AccessMode.READ),
    "get_ticket_summary": ("it_agent", "it_policy_kb", AccessMode.READ),
    "get_ticket_detail": ("it_agent", "it_policy_kb", AccessMode.READ),
    "create_ticket": ("it_agent", "itsm_ticket", AccessMode.PROPOSE_WRITE),
    "request_device": ("it_agent", "itsm_ticket", AccessMode.PROPOSE_WRITE),
    "update_ticket_status": ("it_agent", "itsm_ticket", AccessMode.PROPOSE_WRITE),
    "escalate_ticket": ("it_agent", "itsm_ticket", AccessMode.PROPOSE_WRITE),
    "search_contract": ("legal_agent", "contract_service", AccessMode.READ),
    "check_compliance": ("legal_agent", "legal_policy_kb", AccessMode.READ),
    "query_expense_policy": ("finance_agent", "finance_policy_kb", AccessMode.READ),
    "check_budget": ("finance_agent", "finance_policy_kb", AccessMode.READ),
    "query_salary_structure": ("finance_agent", "finance_policy_kb", AccessMode.READ),
    "check_travel_policy": ("finance_agent", "finance_policy_kb", AccessMode.READ),
    "submit_expense_report": ("finance_agent", "expense_service", AccessMode.PROPOSE_WRITE),
}


def get_expert_profile(expert_id: str) -> ExpertProfile:
    """未知专家无法获得资源清单，调用方应按不可用处理。"""
    return EXPERT_REGISTRY[expert_id]


def resource_access_allowed(expert_id: str, resource_id: str, requested: AccessMode) -> bool:
    """统一资源授权判断；专家永远不能直接取得 execute_write。"""
    profile = EXPERT_REGISTRY.get(expert_id)
    manifest = RESOURCE_REGISTRY.get(resource_id)
    if profile is None or manifest is None or requested is AccessMode.EXECUTE_WRITE:
        return False
    if resource_id not in (*profile.private_resources, *profile.shared_services):
        return False
    if requested is AccessMode.READ:
        return True
    return requested is AccessMode.PROPOSE_WRITE and manifest.access_mode is AccessMode.PROPOSE_WRITE


def expert_id_from_name(name: str) -> str | None:
    if name in EXPERT_REGISTRY:
        return name
    return next((key for key, profile in EXPERT_REGISTRY.items() if profile.display_name == name), None)

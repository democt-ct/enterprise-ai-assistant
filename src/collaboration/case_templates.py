"""可注册的 Case 模板。

模板只描述业务状态下的任务依赖和领域能力，不持有 Agent 实例，也不包含
外部写操作。协调器可以在模板允许的领域内调整协作策略。
"""

from __future__ import annotations

from dataclasses import dataclass

from .case_service import CaseTaskSpec


@dataclass(frozen=True)
class CaseTemplate:
    template_id: str
    title: str
    description: str
    task_specs: tuple[CaseTaskSpec, ...]


ONBOARDING_TEMPLATE = CaseTemplate(
    template_id="employee_onboarding",
    title="新员工入职协同",
    description="协调身份信息、账号设备、协议签署与费用准备的跨部门事务。",
    task_specs=(
        CaseTaskSpec("hr_profile", "确认入职信息", "hr"),
        CaseTaskSpec("it_provisioning", "准备账号与设备", "it", ("hr_profile",)),
        CaseTaskSpec("legal_documents", "准备并签署协议", "legal", ("hr_profile",)),
        CaseTaskSpec("finance_setup", "完成费用与薪资主体确认", "finance", ("hr_profile",)),
    ),
)

AD_HOC_TEMPLATE = CaseTemplate(
    template_id="cross_domain_consultation",
    title="跨部门事务研判",
    description="用于没有预设办理链路的跨领域咨询；专家选择与协作顺序在运行时决定。",
    task_specs=(),
)

_TEMPLATES = {template.template_id: template for template in (ONBOARDING_TEMPLATE, AD_HOC_TEMPLATE)}


def get_template(template_id: str) -> CaseTemplate | None:
    return _TEMPLATES.get(template_id)


def list_templates() -> list[CaseTemplate]:
    return list(_TEMPLATES.values())

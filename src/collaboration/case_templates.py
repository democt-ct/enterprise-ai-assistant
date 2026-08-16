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

OFFBOARDING_TEMPLATE = CaseTemplate(
    template_id="employee_offboarding",
    title="员工离职协同",
    description=(
        "离职结清：逐级推进财务结算与 IT 资产回收，出现未归还资产时阻塞并要求恢复，"
        "不直接产出半成品结论。"
    ),
    task_specs=(
        # 严格线性链式依赖：每一级任务都受上一级产物 gate，证明任务图引擎能处理
        # depends_on 深度 > 1 的多跳调度，而非只在单层并行。
        CaseTaskSpec("hr_clearance", "确认离职结清信息", "hr"),
        CaseTaskSpec("it_offboarding", "回收设备与权限", "it", ("hr_clearance",)),
        CaseTaskSpec("finance_settlement", "结算薪资与费用", "finance", ("it_offboarding",)),
        CaseTaskSpec("legal_clearance", "确认保密与竞业义务", "legal", ("hr_clearance",)),
    ),
)

AD_HOC_TEMPLATE = CaseTemplate(
    template_id="cross_domain_consultation",
    title="跨部门事务研判",
    description="用于没有预设办理链路的跨领域咨询；专家选择与协作顺序在运行时决定。",
    task_specs=(),
)

_TEMPLATES = {
    template.template_id: template
    for template in (ONBOARDING_TEMPLATE, OFFBOARDING_TEMPLATE, AD_HOC_TEMPLATE)
}


def get_template(template_id: str) -> CaseTemplate | None:
    return _TEMPLATES.get(template_id)


def list_templates() -> list[CaseTemplate]:
    return list(_TEMPLATES.values())

"""Case 协作内核的持久化服务。

Case 仅保存任务状态、受控上下文和可审计产物；领域推理仍由 Expert Pod
完成，外部写入仍只能由审批网关执行。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.tools.db.connection import _now, _uid, get_db

from .contracts import ExpertArtifact, OperationProposal


CASE_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"collecting", "planned", "cancelled"},
    "collecting": {"planned", "cancelled"},
    "planned": {"coordinating", "cancelled"},
    "coordinating": {"waiting_input", "waiting_approval", "blocked", "completed", "cancelled"},
    "waiting_input": {"coordinating", "cancelled"},
    "waiting_approval": {"coordinating", "completed", "cancelled"},
    "blocked": {"collecting", "coordinating", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}

TASK_TRANSITIONS: dict[str, set[str]] = {
    "not_started": {"in_progress", "cancelled"},
    "in_progress": {"waiting_input", "waiting_dependency", "waiting_approval", "blocked", "completed", "cancelled"},
    "waiting_input": {"in_progress", "cancelled"},
    "waiting_dependency": {"in_progress", "cancelled"},
    "waiting_approval": {"in_progress", "completed", "cancelled"},
    "blocked": {"in_progress", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


@dataclass(frozen=True)
class CaseTaskSpec:
    key: str
    title: str
    domain: str
    depends_on: tuple[str, ...] = ()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _event(conn: Any, case_id: str, event_type: str, actor_id: str = "system", **payload: Any) -> None:
    conn.execute(
        "INSERT INTO case_events (id, case_id, event_type, actor_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (_uid(), case_id, event_type, actor_id, _json(payload), _now()),
    )


def create_case(
    *,
    owner_user_id: str,
    template_id: str,
    title: str,
    goal: str,
    tasks: list[CaseTaskSpec],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """创建一个 Case 与其初始任务图。

    第一个无依赖任务会进入 ``in_progress``，其余任务等待依赖；这让模板
    声明依赖关系，但不决定专家如何进行领域推理。
    """
    case_id = _uid()
    now = _now()
    # 信息补充由具体任务进入 waiting_input 表示；Case 创建后即可进入计划态。
    status = "planned"
    with get_db() as conn:
        conn.execute(
            """INSERT INTO cases (id, owner_user_id, template_id, title, goal, status, context_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (case_id, owner_user_id, template_id, title, goal, status, _json(context or {}), now, now),
        )
        for spec in tasks:
            task_status = "waiting_dependency" if spec.depends_on else "in_progress"
            conn.execute(
                """INSERT INTO case_tasks (id, case_id, task_key, title, domain, status, depends_on_json, details_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)""",
                (_uid(), case_id, spec.key, spec.title, spec.domain, task_status, _json(list(spec.depends_on)), now, now),
            )
        _event(conn, case_id, "case_created", owner_user_id, template_id=template_id, task_count=len(tasks))
        conn.commit()
    return get_case(case_id) or {}


def list_cases(owner_user_id: str | None = None, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    sql = "SELECT * FROM cases"
    params: list[Any] = []
    if owner_user_id:
        sql += " WHERE owner_user_id = ?"
        params.append(owner_user_id)
    else:
        sql += " WHERE 1 = 1"
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(max(1, min(limit, 100)))
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_case_summary(dict(row)) for row in rows]


def get_case(case_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        case_row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if case_row is None:
            return None
        tasks = conn.execute("SELECT * FROM case_tasks WHERE case_id = ? ORDER BY created_at", (case_id,)).fetchall()
        artifacts = conn.execute("SELECT * FROM case_artifacts WHERE case_id = ? ORDER BY created_at", (case_id,)).fetchall()
        proposals = conn.execute("SELECT * FROM case_operation_proposals WHERE case_id = ? ORDER BY created_at", (case_id,)).fetchall()
        events = conn.execute("SELECT * FROM case_events WHERE case_id = ? ORDER BY created_at", (case_id,)).fetchall()

    case = _case_summary(dict(case_row))
    case["tasks"] = [_task_view(dict(row)) for row in tasks]
    case["artifacts"] = [_artifact_view(dict(row)) for row in artifacts]
    case["operation_proposals"] = [_proposal_view(dict(row)) for row in proposals]
    case["events"] = [_event_view(dict(row)) for row in events]
    return case


def transition_case(case_id: str, target: str, actor_id: str) -> dict[str, Any]:
    with get_db() as conn:
        row = conn.execute("SELECT status FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise KeyError("Case 不存在")
        current = row["status"]
        if target not in CASE_TRANSITIONS.get(current, set()):
            raise ValueError(f"Case 不能从 {current} 变更为 {target}")
        now = _now()
        completed_at = now if target == "completed" else None
        conn.execute(
            "UPDATE cases SET status = ?, updated_at = ?, completed_at = ? WHERE id = ?",
            (target, now, completed_at, case_id),
        )
        _event(conn, case_id, "case_status_changed", actor_id, previous=current, target=target)
        conn.commit()
    return get_case(case_id) or {}


def transition_task(case_id: str, task_id: str, target: str, actor_id: str) -> dict[str, Any]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM case_tasks WHERE id = ? AND case_id = ?", (task_id, case_id)).fetchone()
        if row is None:
            raise KeyError("Case 任务不存在")
        current = row["status"]
        if target not in TASK_TRANSITIONS.get(current, set()):
            raise ValueError(f"任务不能从 {current} 变更为 {target}")
        if target == "in_progress" and not _dependencies_completed(conn, case_id, _loads(row["depends_on_json"], [])):
            raise ValueError("依赖任务尚未完成")
        conn.execute("UPDATE case_tasks SET status = ?, updated_at = ? WHERE id = ?", (target, _now(), task_id))
        _event(conn, case_id, "task_status_changed", actor_id, task_id=task_id, previous=current, target=target)
        conn.commit()
    return get_case(case_id) or {}


def record_artifact(case_id: str, artifact: ExpertArtifact, task_id: str | None = None) -> str:
    """持久化结构化专家产物，并将写操作保存为待确认提案。"""
    artifact_id = _uid()
    now = _now()
    payload = {
        "summary": artifact.summary,
        "facts": artifact.facts,
        "evidence_refs": artifact.evidence_refs,
        "recommendations": artifact.recommendations,
        "risk_flags": artifact.risk_flags,
        "confidence": artifact.confidence,
        "next_actions": artifact.next_actions,
    }
    with get_db() as conn:
        conn.execute(
            """INSERT INTO case_artifacts (id, case_id, task_id, expert_id, share_scope, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (artifact_id, case_id, task_id, artifact.expert_id, artifact.share_scope.value, _json(payload), now),
        )
        for proposal in artifact.operation_proposals:
            _insert_proposal(conn, case_id, artifact_id, task_id, proposal, now)
        _event(conn, case_id, "artifact_recorded", artifact.expert_id, task_id=task_id, share_scope=artifact.share_scope.value)
        conn.commit()
    return artifact_id


def confirm_proposal(case_id: str, proposal_id: str, actor_id: str) -> dict[str, Any]:
    """确认操作提案；实际执行必须由后续审批网关适配器完成。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT status, approval_required FROM case_operation_proposals WHERE id = ? AND case_id = ?",
            (proposal_id, case_id),
        ).fetchone()
        if row is None:
            raise KeyError("操作提案不存在")
        if row["status"] != "proposed":
            raise ValueError("该操作提案当前不可确认")
        target = "pending_approval" if row["approval_required"] else "confirmed"
        conn.execute(
            "UPDATE case_operation_proposals SET status = ?, updated_at = ? WHERE id = ?",
            (target, _now(), proposal_id),
        )
        _event(conn, case_id, "operation_proposal_confirmed", actor_id, proposal_id=proposal_id, target=target)
        conn.commit()
    return get_case(case_id) or {}


def prepare_onboarding(case_id: str, actor_id: str, details: dict[str, Any]) -> dict[str, Any]:
    """确认入职资料后，生成各部门的受控 Mock 操作提案。"""
    required = ("start_date", "position")
    missing = [key for key in required if not str(details.get(key, "")).strip()]
    if missing:
        raise ValueError("缺少入职信息：" + "、".join(missing))

    with get_db() as conn:
        case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if case is None or case["template_id"] != "employee_onboarding":
            raise ValueError("该 Case 不是入职协同事项")
        if case["status"] != "planned":
            raise ValueError("入职资料只能在计划阶段确认")
        tasks = {row["task_key"]: dict(row) for row in conn.execute("SELECT * FROM case_tasks WHERE case_id = ?", (case_id,))}
        if "hr_profile" not in tasks:
            raise ValueError("入职任务图不完整")
        now = _now()
        context = _loads(case["context_json"], {})
        context.update({key: details[key] for key in ("start_date", "position", "department") if details.get(key)})
        conn.execute("UPDATE cases SET context_json = ?, status = 'coordinating', updated_at = ? WHERE id = ?", (_json(context), now, case_id))
        conn.execute("UPDATE case_tasks SET status = 'completed', updated_at = ? WHERE id = ?", (now, tasks["hr_profile"]["id"]))
        _event(conn, case_id, "onboarding_profile_confirmed", actor_id, start_date=context["start_date"], position=context["position"])
        _insert_artifact(conn, case_id, tasks["hr_profile"]["id"], "hr_agent", "team", {
            "summary": "入职日期与岗位信息已确认，已启动相关部门协作。",
            "facts": [f"入职日期：{context['start_date']}", f"岗位：{context['position']}"],
        }, [], now)
        proposals = {
            "it_provisioning": ("it_agent", "identity_provisioning", "provision_account", "准备企业邮箱、VPN 权限与设备"),
            "legal_documents": ("legal_agent", "nda_service", "prepare_nda", "生成并发送保密协议"),
            "finance_setup": ("finance_agent", "payroll_service", "configure_payroll_profile", "配置薪资主体与报销权限"),
        }
        for task_key, (expert_id, resource_id, action, summary) in proposals.items():
            task = tasks[task_key]
            conn.execute("UPDATE case_tasks SET status = 'waiting_approval', updated_at = ? WHERE id = ?", (now, task["id"]))
            proposal = OperationProposal(resource_id=resource_id, action=action, summary=summary, parameters={"position": context["position"], "start_date": context["start_date"]})
            _insert_artifact(conn, case_id, task["id"], expert_id, "team", {"summary": f"已形成操作提案：{summary}", "facts": [], "recommendations": ["等待发起人确认与管理员审批"]}, [proposal], now)
        _event(conn, case_id, "onboarding_proposals_created", "system", proposal_count=3)
        conn.commit()
    return get_case(case_id) or {}


def approve_and_execute_mock_proposal(case_id: str, proposal_id: str, actor_id: str) -> dict[str, Any]:
    """审批网关的 Mock 执行：仅记录可审计结果，不调用真实企业系统。"""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM case_operation_proposals WHERE id = ? AND case_id = ?", (proposal_id, case_id)).fetchone()
        if row is None:
            raise KeyError("操作提案不存在")
        if row["status"] != "pending_approval":
            raise ValueError("操作提案尚未完成用户确认")
        now = _now()
        conn.execute("UPDATE case_operation_proposals SET status = 'executed', updated_at = ? WHERE id = ?", (now, proposal_id))
        if row["task_id"]:
            conn.execute("UPDATE case_tasks SET status = 'completed', updated_at = ? WHERE id = ?", (now, row["task_id"]))
        _event(conn, case_id, "mock_operation_executed", actor_id, proposal_id=proposal_id, resource_id=row["resource_id"], action=row["action"])
        unfinished = conn.execute("SELECT COUNT(*) AS count FROM case_tasks WHERE case_id = ? AND status != 'completed'", (case_id,)).fetchone()["count"]
        if unfinished == 0:
            conn.execute("UPDATE cases SET status = 'completed', updated_at = ?, completed_at = ? WHERE id = ?", (now, now, case_id))
            _event(conn, case_id, "case_completed", "system")
        conn.commit()
    return get_case(case_id) or {}


def prepare_offboarding(case_id: str, actor_id: str, details: dict[str, Any]) -> dict[str, Any]:
    """确认离职结清信息后，逐级生成 IT 资产回收与财务结算的受控提案。

    与入职不同的是，离职采用严格链式依赖：财务结算任务 `finance_settlement`
    依赖 `it_offboarding`，因此必须先完成资产回收 / 权限回收才能结算，突出
    depends_on 深度 > 1 的多跳调度。
    """
    required = ("last_workday", "reason")
    missing = [key for key in required if not str(details.get(key, "")).strip()]
    if missing:
        raise ValueError("缺少离职信息：" + "、".join(missing))

    with get_db() as conn:
        case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if case is None or case["template_id"] != "employee_offboarding":
            raise ValueError("该 Case 不是离职协同事项")
        if case["status"] != "planned":
            raise ValueError("离职资料只能在计划阶段确认")
        tasks = {row["task_key"]: dict(row) for row in conn.execute("SELECT * FROM case_tasks WHERE case_id = ?", (case_id,))}
        if "hr_clearance" not in tasks:
            raise ValueError("离职任务图不完整")

        context = _loads(case["context_json"], {})
        context.update({key: details[key] for key in ("last_workday", "reason") if details.get(key)})
        conn.execute("UPDATE cases SET context_json = ?, status = 'coordinating', updated_at = ? WHERE id = ?", (_json(context), _now(), case_id))
        conn.execute("UPDATE case_tasks SET status = 'completed', updated_at = ? WHERE id = ?", (_now(), tasks["hr_clearance"]["id"]))
        _event(conn, case_id, "offboarding_clearance_confirmed", actor_id, last_workday=context["last_workday"])

        _insert_artifact(conn, case_id, tasks["hr_clearance"]["id"], "hr_agent", "team", {
            "summary": "离职结清信息已确认，已启动资产回收与结算协同。",
            "facts": [f"最后工作日：{context['last_workday']}", f"离职原因：{context['reason']}"],
        }, [], _now())

        # IT 资产回收入口先就绪；财务结算任务被 it_offboarding 依赖，
        # 在资产回收完成前不会进入 in_progress（create_case 已置 waiting_dependency；
        # 此处仅对法律义务确认任务与资产回收生成提案）。
        if "it_offboarding" in tasks:
            conn.execute("UPDATE case_tasks SET status = 'waiting_approval', updated_at = ? WHERE id = ?", (_now(), tasks["it_offboarding"]["id"]))
            it_proposal = OperationProposal(
                resource_id="itsm_ticket", action="reclaim_assets",
                summary="回收设备与回收权限", parameters={"last_workday": context["last_workday"]},
            )
            _insert_artifact(conn, case_id, tasks["it_offboarding"]["id"], "it_agent", "team",
                {"summary": "已形成 IT 资产回收操作提案", "facts": [], "recommendations": ["等待资产回收审批执行"]},
                [it_proposal], _now())

        if "legal_clearance" in tasks:
            conn.execute("UPDATE case_tasks SET status = 'waiting_approval', updated_at = ? WHERE id = ?", (_now(), tasks["legal_clearance"]["id"]))
            legal_proposal = OperationProposal(
                resource_id="contract_service", action="confirm_offboarding_obligations",
                summary="确认保密与竞业义务签署",
            )
            _insert_artifact(conn, case_id, tasks["legal_clearance"]["id"], "legal_agent", "team",
                {"summary": "已形成离职义务确认提案", "facts": [], "recommendations": ["等待法务义务确认审批"]},
                [legal_proposal], _now())

        # 财务结算待资产回收完成后再由 approve 链路推进
        _event(conn, case_id, "offboarding_proposals_created", "system", proposal_count=2, finance_gated_by="it_offboarding")
        conn.commit()
    return get_case(case_id) or {}


def recover_offboarding(case_id: str, actor_id: str) -> dict[str, Any]:
    """资产回收失败后的恢复入口：允许从 blocked 回到 coordinating 重新推进。"""
    with get_db() as conn:
        case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if case is None:
            raise KeyError("Case 不存在")
        if case["status"] != "blocked":
            raise ValueError("只有阻塞中的离职 Case 可以恢复")
        block_target = _now()
        conn.execute("UPDATE cases SET status = 'coordinating', updated_at = ? WHERE id = ?", (block_target, case_id))
        _event(conn, case_id, "offboarding_recovered", actor_id)
        conn.commit()
    return get_case(case_id) or {}


def block_offboarding_for_missing_asset(case_id: str, actor_id: str, reason: str) -> dict[str, Any]:
    """模拟资产回收失败：把 IT 任务与 Case 置为 blocked。

    财务结算任务被 it_offboarding 依赖，因此在资产回收未完成前不能进入结算；
    这一失败把 Case 整体阻塞而不是返回半成品结论，符合"失败边界"主张。
    """
    with get_db() as conn:
        case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if case is None or case["template_id"] != "employee_offboarding":
            raise ValueError("该 Case 不是离职协同事项")
        it_task = conn.execute(
            "SELECT * FROM case_tasks WHERE case_id = ? AND task_key = 'it_offboarding'",
            (case_id,),
        ).fetchone()
        if it_task is None:
            raise ValueError("离职任务图缺少资产回收任务")
        if it_task["status"] not in {"in_progress", "waiting_approval"}:
            raise ValueError("资产回收任务当前不可置为失败")
        now = _now()
        conn.execute("UPDATE case_tasks SET status = 'blocked', updated_at = ? WHERE id = ?", (now, it_task["id"]))
        conn.execute("UPDATE cases SET status = 'blocked', updated_at = ? WHERE id = ?", (now, case_id))
        _event(conn, case_id, "offboarding_asset_reclaim_failed", actor_id, reason=reason, task_id=it_task["id"])
        conn.commit()
    return get_case(case_id) or {}


def complete_asset_reclaim(case_id: str, actor_id: str, closed: bool = False) -> dict[str, Any]:
    """资产回收完成 → 自动解锁并启动被其依赖的财务结算任务。

    这是严格链式依赖的直接证据：完成 it_offboarding 后，等待它的
    finance_settlement 从 waiting_dependency 进入可执行状态。
    """
    with get_db() as conn:
        case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if case is None or case["template_id"] != "employee_offboarding":
            raise ValueError("该 Case 不是离职协同事项")
        it_task = conn.execute(
            "SELECT * FROM case_tasks WHERE case_id = ? AND task_key = 'it_offboarding'",
            (case_id,),
        ).fetchone()
        if it_task is None:
            raise ValueError("离职任务图缺少资产回收任务")
        if it_task["status"] != "blocked":
            raise ValueError("资产回收任务不在失败态，无需恢复")
        if not closed:
            raise ValueError("资产尚未归还，无法完成回收")
        finance_task = conn.execute(
            "SELECT * FROM case_tasks WHERE case_id = ? AND task_key = 'finance_settlement'",
            (case_id,),
        ).fetchone()
        now = _now()
        conn.execute("UPDATE case_tasks SET status = 'completed', updated_at = ? WHERE id = ?", (now, it_task["id"]))
        if finance_task is not None:
            # 依赖已满足：允许财务从 waiting_dependency → in_progress → 生成结算提案
            conn.execute("UPDATE case_tasks SET status = 'in_progress', updated_at = ? WHERE id = ?", (now, finance_task["id"]))
            finance_proposal = OperationProposal(
                resource_id="payroll_service", action="settle_payroll",
                summary="结算离职薪资与未报销费用",
            )
            _insert_artifact(conn, case_id, finance_task["id"], "finance_agent", "team",
                {"summary": "资产已回收，启动离职薪资结算", "facts": [], "recommendations": ["等待结算审批执行"]},
                [finance_proposal], now)
        if case["status"] == "blocked":
            conn.execute("UPDATE cases SET status = 'coordinating', updated_at = ? WHERE id = ?", (now, case_id))
            _event(conn, case_id, "offboarding_recovered", actor_id)
        _event(conn, case_id, "offboarding_asset_reclaimed", actor_id, task_id=it_task["id"])
        conn.commit()
    return get_case(case_id) or {}


def _dependencies_completed(conn: Any, case_id: str, task_keys: list[str]) -> bool:
    if not task_keys:
        return True
    placeholders = ",".join("?" for _ in task_keys)
    rows = conn.execute(
        f"SELECT task_key, status FROM case_tasks WHERE case_id = ? AND task_key IN ({placeholders})",
        [case_id, *task_keys],
    ).fetchall()
    return len(rows) == len(task_keys) and all(row["status"] == "completed" for row in rows)


def _insert_proposal(conn: Any, case_id: str, artifact_id: str, task_id: str | None, proposal: OperationProposal, now: str) -> None:
    conn.execute(
        """INSERT INTO case_operation_proposals (id, case_id, artifact_id, task_id, resource_id, action, summary, parameters_json, approval_required, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)""",
        (_uid(), case_id, artifact_id, task_id, proposal.resource_id, proposal.action, proposal.summary,
         _json(proposal.parameters), int(proposal.required_approval), now, now),
    )


def _insert_artifact(conn: Any, case_id: str, task_id: str | None, expert_id: str, share_scope: str, payload: dict[str, Any], proposals: list[OperationProposal], now: str) -> str:
    artifact_id = _uid()
    conn.execute(
        "INSERT INTO case_artifacts (id, case_id, task_id, expert_id, share_scope, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (artifact_id, case_id, task_id, expert_id, share_scope, _json(payload), now),
    )
    for proposal in proposals:
        _insert_proposal(conn, case_id, artifact_id, task_id, proposal, now)
    return artifact_id


def _case_summary(row: dict[str, Any]) -> dict[str, Any]:
    row["context"] = _loads(row.pop("context_json", None), {})
    return row


def _task_view(row: dict[str, Any]) -> dict[str, Any]:
    row["depends_on"] = _loads(row.pop("depends_on_json", None), [])
    row["details"] = _loads(row.pop("details_json", None), {})
    return row


def _artifact_view(row: dict[str, Any]) -> dict[str, Any]:
    row["payload"] = _loads(row.pop("payload_json", None), {})
    return row


def _proposal_view(row: dict[str, Any]) -> dict[str, Any]:
    row["parameters"] = _loads(row.pop("parameters_json", None), {})
    row["approval_required"] = bool(row["approval_required"])
    return row


def _event_view(row: dict[str, Any]) -> dict[str, Any]:
    row["payload"] = _loads(row.pop("payload_json", None), {})
    return row

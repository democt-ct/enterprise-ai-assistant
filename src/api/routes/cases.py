"""Case 协作中心 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.collaboration.case_service import (
    approve_and_execute_mock_proposal,
    block_offboarding_for_missing_asset,
    complete_asset_reclaim,
    create_case,
    confirm_proposal,
    get_case,
    list_cases,
    prepare_offboarding,
    prepare_onboarding,
    transition_case,
    transition_task,
)
from src.collaboration.case_templates import get_template, list_templates
from src.gateway.auth import SessionContext


router = APIRouter(tags=["cases"])


class CreateCaseRequest(BaseModel):
    template_id: str = Field(..., description="已注册的 Case 模板 ID")
    goal: str = Field(..., min_length=3, max_length=1000, description="用户的事务目标")
    title: str | None = Field(default=None, max_length=120)
    context: dict[str, Any] = Field(default_factory=dict, description="经用户确认可用于本 Case 的补充上下文")


class TransitionRequest(BaseModel):
    status: str = Field(..., min_length=2, max_length=32)


class OnboardingDetailsRequest(BaseModel):
    start_date: str = Field(..., min_length=10, max_length=10)
    position: str = Field(..., min_length=2, max_length=80)
    department: str | None = Field(default=None, max_length=80)


class OffboardingDetailsRequest(BaseModel):
    last_workday: str = Field(..., min_length=10, max_length=10)
    reason: str = Field(..., min_length=2, max_length=200)


def _session(request: Request) -> SessionContext | None:
    return getattr(request.state, "session", None)


def _can_view(case: dict[str, Any], session: SessionContext) -> bool:
    return case["owner_user_id"] == session.user_id or session.role in {"hr", "manager", "admin"}


def _is_hr_admin(session: SessionContext) -> bool:
    return session.role in {"hr", "admin"} or (
        session.role == "manager" and getattr(session, "department_name", "") == "HR部"
    )


def _case_for_employee(case: dict[str, Any]) -> dict[str, Any]:
    """普通工作台不返回专家私有产物或原始内部上下文。"""
    case = dict(case)
    case["context"] = {
        key: value for key, value in case.get("context", {}).items()
        if key in {"department", "role", "start_date", "position", "last_workday", "reason"}
    }
    case["artifacts"] = [
        artifact for artifact in case.get("artifacts", [])
        if artifact.get("share_scope") != "private"
    ]
    return case


@router.get("/case-templates")
async def case_templates(request: Request):
    if _session(request) is None:
        return JSONResponse(status_code=401, content={"error": "未认证"})
    return {
        "templates": [
            {
                "template_id": template.template_id,
                "title": template.title,
                "description": template.description,
                "domains": sorted({task.domain for task in template.task_specs}),
            }
            for template in list_templates()
        ]
    }


@router.post("/cases", status_code=201)
async def create_case_endpoint(body: CreateCaseRequest, request: Request):
    session = _session(request)
    if session is None:
        return JSONResponse(status_code=401, content={"error": "未认证"})
    template = get_template(body.template_id)
    if template is None:
        return JSONResponse(status_code=400, content={"error": "未知的 Case 模板"})
    safe_context = {key: value for key, value in body.context.items() if key in {"department", "role", "start_date", "position", "last_workday", "reason"}}
    case = create_case(
        owner_user_id=session.user_id,
        template_id=template.template_id,
        title=body.title or template.title,
        goal=body.goal,
        tasks=list(template.task_specs),
        context=safe_context,
    )
    return {"case": _case_for_employee(case)}


@router.get("/cases")
async def list_cases_endpoint(request: Request, status: str | None = None):
    session = _session(request)
    if session is None:
        return JSONResponse(status_code=401, content={"error": "未认证"})
    owner_user_id = None if _is_hr_admin(session) else session.user_id
    return {"cases": list_cases(owner_user_id, status=status)}


@router.get("/cases/{case_id}")
async def get_case_endpoint(case_id: str, request: Request):
    session = _session(request)
    if session is None:
        return JSONResponse(status_code=401, content={"error": "未认证"})
    case = get_case(case_id)
    if case is None:
        return JSONResponse(status_code=404, content={"error": "Case 不存在"})
    if not _can_view(case, session):
        return JSONResponse(status_code=403, content={"error": "无权查看此 Case"})
    return {"case": case}


@router.post("/cases/{case_id}/transition")
async def transition_case_endpoint(case_id: str, body: TransitionRequest, request: Request):
    session = _session(request)
    if session is None:
        return JSONResponse(status_code=401, content={"error": "未认证"})
    case = get_case(case_id)
    if case is None:
        return JSONResponse(status_code=404, content={"error": "Case 不存在"})
    if not _can_view(case, session):
        return JSONResponse(status_code=403, content={"error": "无权操作此 Case"})
    try:
        return {"case": _case_for_employee(transition_case(case_id, body.status, session.user_id))}
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/cases/{case_id}/tasks/{task_id}/transition")
async def transition_task_endpoint(case_id: str, task_id: str, body: TransitionRequest, request: Request):
    session = _session(request)
    if session is None:
        return JSONResponse(status_code=401, content={"error": "未认证"})
    case = get_case(case_id)
    if case is None:
        return JSONResponse(status_code=404, content={"error": "Case 不存在"})
    if not _can_view(case, session):
        return JSONResponse(status_code=403, content={"error": "无权操作此 Case"})
    try:
        return {"case": _case_for_employee(transition_task(case_id, task_id, body.status, session.user_id))}
    except (KeyError, ValueError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/cases/{case_id}/proposals/{proposal_id}/confirm")
async def confirm_proposal_endpoint(case_id: str, proposal_id: str, request: Request):
    session = _session(request)
    if session is None:
        return JSONResponse(status_code=401, content={"error": "未认证"})
    case = get_case(case_id)
    if case is None:
        return JSONResponse(status_code=404, content={"error": "Case 不存在"})
    if case["owner_user_id"] != session.user_id and not _is_hr_admin(session):
        return JSONResponse(status_code=403, content={"error": "只有发起人或事务管理员可以确认操作"})
    try:
        return {"case": _case_for_employee(confirm_proposal(case_id, proposal_id, session.user_id))}
    except (KeyError, ValueError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/cases/{case_id}/onboarding/prepare")
async def prepare_onboarding_endpoint(case_id: str, body: OnboardingDetailsRequest, request: Request):
    session = _session(request)
    if session is None:
        return JSONResponse(status_code=401, content={"error": "未认证"})
    case = get_case(case_id)
    if case is None:
        return JSONResponse(status_code=404, content={"error": "Case 不存在"})
    if case["owner_user_id"] != session.user_id and not _is_hr_admin(session):
        return JSONResponse(status_code=403, content={"error": "只有发起人或 HR 可以确认入职资料"})
    try:
        return {"case": _case_for_employee(prepare_onboarding(case_id, session.user_id, body.model_dump()))}
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/cases/{case_id}/offboarding/prepare")
async def prepare_offboarding_endpoint(case_id: str, body: OffboardingDetailsRequest, request: Request):
    session = _session(request)
    if session is None:
        return JSONResponse(status_code=401, content={"error": "未认证"})
    case = get_case(case_id)
    if case is None:
        return JSONResponse(status_code=404, content={"error": "Case 不存在"})
    if case["owner_user_id"] != session.user_id and not _is_hr_admin(session):
        return JSONResponse(status_code=403, content={"error": "只有发起人或 HR 可以确认离职信息"})
    try:
        return {"case": _case_for_employee(prepare_offboarding(case_id, session.user_id, body.model_dump()))}
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/cases/{case_id}/offboarding/asset-fail")
async def offboarding_asset_fail_endpoint(case_id: str, request: Request):
    """演示资产回收失败→Case 阻塞：财务结算因依赖未满足而不能进入。"""
    session = _session(request)
    if session is None:
        return JSONResponse(status_code=401, content={"error": "未认证"})
    if not _is_hr_admin(session):
        return JSONResponse(status_code=403, content={"error": "只有 HR 或管理员可以发起资产回收失败"})
    try:
        return {"case": _case_for_employee(block_offboarding_for_missing_asset(case_id, session.user_id, "笔记本未归还"))}
    except (KeyError, ValueError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/cases/{case_id}/offboarding/asset-done")
async def offboarding_asset_done_endpoint(case_id: str, request: Request):
    """资产回收完成 → 解锁被其依赖的财务结算任务，并新建结算提案。"""
    session = _session(request)
    if session is None:
        return JSONResponse(status_code=401, content={"error": "未认证"})
    if not _is_hr_admin(session):
        return JSONResponse(status_code=403, content={"error": "只有 HR 或管理员可以确认资产回收"})
    try:
        return {"case": _case_for_employee(complete_asset_reclaim(case_id, session.user_id, closed=True))}
    except (KeyError, ValueError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@router.post("/cases/{case_id}/proposals/{proposal_id}/approve-mock")
async def approve_mock_proposal_endpoint(case_id: str, proposal_id: str, request: Request):
    """作品集演示用的审批网关 Mock：仅 HR/admin 可调用。"""
    session = _session(request)
    if session is None:
        return JSONResponse(status_code=401, content={"error": "未认证"})
    if not _is_hr_admin(session):
        return JSONResponse(status_code=403, content={"error": "只有 HR 或管理员可以审批执行"})
    try:
        return {"case": _case_for_employee(approve_and_execute_mock_proposal(case_id, proposal_id, session.user_id))}
    except (KeyError, ValueError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

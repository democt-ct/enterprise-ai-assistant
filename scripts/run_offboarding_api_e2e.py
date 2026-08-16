"""离职 API E2E 验证（使用工作区 data/ 下的临时库，避开系统 Temp 沙箱限制）。"""
from __future__ import annotations

import os
from pathlib import Path

os.environ["APP_DB_PATH"] = str(Path("data").resolve() / "e2e_offboard.db")
os.environ["SKIP_AGENT_INIT"] = "true"

from src.tools.db import bootstrap_company_workspace  # noqa: E402
from src.tools.db.connection import close_db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from src.api.app import create_app  # noqa: E402


def _ok(resp, code=200):
    assert resp.status_code == code, (resp.status_code, resp.text)
    return resp.json()


def main() -> None:
    bootstrap_company_workspace()
    close_db()
    with TestClient(create_app()) as client:
        emp = _ok(client.post("/api/auth/login", json={"user_id": "EMP001", "password": "123456"}))
        eh = {"Authorization": f"Bearer {emp['token']}"}
        created = _ok(client.post("/api/cases", headers=eh, json={
            "template_id": "employee_offboarding", "goal": "办理员工离职",
        }), 201)["case"]
        prepared = _ok(client.post(f"/api/cases/{created['id']}/offboarding/prepare", headers=eh, json={
            "last_workday": "2026-07-31", "reason": "个人原因",
        }))["case"]
        # 资产回收失败/完成需要 HR 角色触发（与入职审批一样是 HR 边界）
        hr = _ok(client.post("/api/auth/login", json={"user_id": "EMP003", "password": "123456"}))
        hh = {"Authorization": f"Bearer {hr['token']}"}
        blocked = _ok(client.post(f"/api/cases/{created['id']}/offboarding/asset-fail", headers=hh))["case"]
        assert blocked["status"] == "blocked", blocked["status"]
        recovered = _ok(client.post(f"/api/cases/{created['id']}/offboarding/asset-done", headers=hh))["case"]
        assert recovered["status"] == "coordinating", recovered["status"]
        payroll = [p for p in recovered["operation_proposals"] if p["resource_id"] == "payroll_service"]
        assert payroll, "payroll proposal should exist after asset done"
        print(f"OFFBOARDING E2E PASS: case={created['id']} tasks={len(recovered['tasks'])} "
              f"proposals={len(recovered['operation_proposals'])} status={recovered['status']}")

    for suffix in ("", "-wal", "-shm"):
        try:
            Path("data").joinpath("e2e_offboard.db" + suffix).unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""使用临时 SQLite 数据库验证登录、Case 创建、确认和 HR Mock 审批。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _ok(response, expected: int = 200):
    if response.status_code != expected:
        raise RuntimeError(f"{response.request.method} {response.request.url}: {response.status_code} {response.text}")
    return response.json()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="enterprise-case-e2e-") as tmp:
        os.environ["APP_DB_PATH"] = str(Path(tmp) / "e2e.db")
        os.environ["SKIP_AGENT_INIT"] = "true"
        from src.tools.db import bootstrap_company_workspace
        bootstrap_company_workspace()
        from src.tools.db.connection import close_db
        close_db()
        from fastapi.testclient import TestClient
        from src.api.app import create_app

        with TestClient(create_app()) as client:
            employee = _ok(client.post("/api/auth/login", json={"user_id": "EMP001", "password": "123456"}))
            employee_headers = {"Authorization": f"Bearer {employee['token']}"}
            created = _ok(client.post("/api/cases", headers=employee_headers, json={
                "template_id": "employee_onboarding", "goal": "为后端开发工程师办理入职",
            }), 201)["case"]
            prepared = _ok(client.post(f"/api/cases/{created['id']}/onboarding/prepare", headers=employee_headers, json={
                "start_date": "2026-09-01", "position": "后端开发工程师", "department": "工程部",
            }))["case"]
            for proposal in prepared["operation_proposals"]:
                _ok(client.post(f"/api/cases/{created['id']}/proposals/{proposal['id']}/confirm", headers=employee_headers))

            hr = _ok(client.post("/api/auth/login", json={"user_id": "EMP003", "password": "123456"}))
            hr_headers = {"Authorization": f"Bearer {hr['token']}"}
            visible = _ok(client.get("/api/cases", headers=hr_headers))["cases"]
            if created["id"] not in {case["id"] for case in visible}:
                raise RuntimeError("HR 工作台未看到员工创建的 Case")
            final = None
            for proposal in prepared["operation_proposals"]:
                final = _ok(client.post(f"/api/cases/{created['id']}/proposals/{proposal['id']}/approve-mock", headers=hr_headers))["case"]
            if final["status"] != "completed":
                raise RuntimeError(f"Case 未完成: {final['status']}")
            print(f"API Case E2E PASS: case={created['id']} tasks={len(final['tasks'])} proposals={len(final['operation_proposals'])}")


if __name__ == "__main__":
    main()

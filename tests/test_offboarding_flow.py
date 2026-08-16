"""离职协同（员工离职）的多跳依赖、失败阻塞与恢复的持久化回归测试。

覆盖"严格链式依赖 depended-on depth > 1"：finance_settlement 依赖
it_offboarding、它又依赖 hr_clearance。资产回收失败会把 Case 置为 blocked，
财务结算不能进入；资产回收完成则自动解锁并生成结算提案。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

import src.tools.db as db
from src.collaboration.case_service import (
    create_case,
    prepare_offboarding,
    block_offboarding_for_missing_asset,
    complete_asset_reclaim,
    get_case,
)
from src.collaboration.case_templates import OFFBOARDING_TEMPLATE, get_template


class OffboardingFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 在仓库 data/ 下使用固定测试库，避免 Windows 沙箱对系统 Temp 的 chmod 限制。
        cls._test_db = Path(__file__).resolve().parent.parent / "data" / "test_offboarding.db"
        cls._test_db.parent.mkdir(parents=True, exist_ok=True)
        os.environ["APP_DB_PATH"] = str(cls._test_db)
        os.environ["SKIP_AGENT_INIT"] = "true"
        db.bootstrap_company_workspace()
        from src.tools.db.connection import close_db
        close_db()
        cls.owner = None

    @classmethod
    def tearDownClass(cls):
        from src.tools.db.connection import close_db
        close_db()
        # 清理连接后再删除，避免 SQLite WAL 句柄锁住文件
        for suffix in ("", "-wal", "-shm"):
            try:
                cls._test_db.with_name(cls._test_db.name + suffix).unlink(missing_ok=True)
            except OSError:
                pass

    def _make_case(self):
        return create_case(
            owner_user_id="EMP001",
            template_id="employee_offboarding",
            title=OFFBOARDING_TEMPLATE.title,
            goal="办理员工离职",
            tasks=list(OFFBOARDING_TEMPLATE.task_specs),
        )

    def test_template_registers_linear_three_level_dependency(self):
        template = get_template("employee_offboarding")
        self.assertIsNotNone(template)
        by_key = {spec.key: set(spec.depends_on) for spec in template.task_specs}
        self.assertEqual(set(), by_key["hr_clearance"])
        self.assertEqual({"hr_clearance"}, by_key["it_offboarding"])
        # 关键断言：财务结算依赖资产回收，构成深度为 2 的严格链式依赖
        self.assertEqual({"it_offboarding"}, by_key["finance_settlement"])
        self.assertEqual({"hr_clearance"}, by_key["legal_clearance"])

    def test_create_case_gates_dependent_tasks_to_waiting(self):
        case = self._make_case()
        task_by_key = {t["task_key"]: t for t in case["tasks"]}
        # create_case 把带依赖的任务初始化为 waiting_dependency
        self.assertEqual("in_progress", task_by_key["hr_clearance"]["status"])
        self.assertEqual("waiting_dependency", task_by_key["it_offboarding"]["status"])
        self.assertEqual("waiting_dependency", task_by_key["finance_settlement"]["status"])
        self.assertEqual("waiting_dependency", task_by_key["legal_clearance"]["status"])

    def test_prepare_offboarding_generates_gated_proposals(self):
        case = self._make_case()
        case = prepare_offboarding(case["id"], "EMP001", {
            "last_workday": "2026-07-31", "reason": "个人原因",
        })
        proposals = {p["resource_id"]: p for p in case["operation_proposals"]}
        self.assertIn("itsm_ticket", proposals)      # IT 资产回收入口就绪
        self.assertIn("contract_service", proposals)  # 法务义务确认入口就绪
        task_by_key = {t["task_key"]: t for t in case["tasks"]}
        self.assertEqual("completed", task_by_key["hr_clearance"]["status"])
        # 财务结算仍被资产回收依赖 gate 住，未生成提案
        self.assertNotIn("payroll_service", proposals)
        self.assertEqual("waiting_dependency", task_by_key["finance_settlement"]["status"])

    def test_asset_reclaim_failure_blocks_case_and_keeps_finance_gated(self):
        case = self._make_case()
        case = prepare_offboarding(case["id"], "EMP001", {
            "last_workday": "2026-07-31", "reason": "个人原因",
        })
        case = block_offboarding_for_missing_asset(case["id"], "EMP003", reason="笔记本未归还")
        self.assertEqual("blocked", case["status"])
        task_by_key = {t["task_key"]: t for t in case["tasks"]}
        self.assertEqual("blocked", task_by_key["it_offboarding"]["status"])
        # 失败不破坏依赖 gate：财务结算仍未进入、也没有结算提案
        self.assertEqual("waiting_dependency", task_by_key["finance_settlement"]["status"])
        self.assertNotIn(
            "payroll_service",
            {p["resource_id"] for p in case["operation_proposals"]},
        )

    def test_asset_reclaim_completion_recovers_and_unlocks_finance(self):
        case = self._make_case()
        case = prepare_offboarding(case["id"], "EMP001", {
            "last_workday": "2026-07-31", "reason": "个人原因",
        })
        case = block_offboarding_for_missing_asset(case["id"], "EMP003", reason="笔记本未归还")
        # 资产收回后驱动恢复：it → completed，依赖它的 finance 从 waiting → in_progress 并生成结算提案
        case = complete_asset_reclaim(case["id"], "EMP003", closed=True)
        task_by_key = {t["task_key"]: t for t in case["tasks"]}
        self.assertEqual("completed", task_by_key["it_offboarding"]["status"])
        self.assertEqual("in_progress", task_by_key["finance_settlement"]["status"])
        self.assertIn("payroll_service", {p["resource_id"] for p in case["operation_proposals"]})
        self.assertEqual("coordinating", case["status"])

    def test_cross_domain_write_proposal_still_requires_approval(self):
        from src.collaboration.registry import resource_access_allowed
        from src.collaboration.contracts import AccessMode
        # 离职写操作（结算/回收）仍只能 propose，专家永远不能 execute_write
        self.assertTrue(resource_access_allowed("finance_agent", "payroll_service", AccessMode.PROPOSE_WRITE))
        self.assertFalse(resource_access_allowed("finance_agent", "payroll_service", AccessMode.EXECUTE_WRITE))


if __name__ == "__main__":
    unittest.main()

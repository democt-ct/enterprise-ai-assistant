from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from src.collaboration.resource_gateway import ResourceGateway
from src.tools.base import ToolDef


def _tool(name: str) -> ToolDef:
    return ToolDef(name=name, description=name, parameters={}, implementation=Mock(return_value={"executed": True}), audit=False)


class ResourceGatewayTests(unittest.TestCase):
    def test_write_tool_returns_proposal_without_calling_implementation(self):
        tool = _tool("create_ticket")
        result = ResourceGateway().execute("IT 专家", tool, {"description": "蓝屏"})

        self.assertEqual("proposed", result["status"])
        tool.implementation.assert_not_called()

    def test_cross_domain_tool_is_denied(self):
        result = ResourceGateway().execute("HR 专家", _tool("create_ticket"), {})
        self.assertTrue(result["permission_denied"])

    def test_read_tool_delegates_to_audited_tool_agent(self):
        tool = _tool("check_ticket_status")
        executed = Mock(data={"status": "处理中"}, error=None, permission_denied=False)
        with patch("src.collaboration.resource_gateway.get_tool_agent") as factory:
            factory.return_value.execute.return_value = executed
            result = ResourceGateway().execute("IT 专家", tool, {"ticket_id": "T1"})

        self.assertEqual("处理中", result["status"])
        factory.return_value.execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()

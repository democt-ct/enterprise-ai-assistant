"""所有 Expert Pod 工具调用的唯一资源授权入口。"""

from __future__ import annotations

from typing import Any

from src.agents.tool_agent import get_tool_agent
from src.tools.base import ToolDef

from .contracts import AccessMode
from .registry import TOOL_RESOURCE_BINDINGS, expert_id_from_name, resource_access_allowed


class ResourceGateway:
    def execute(self, expert_name: str, tool: ToolDef, arguments: dict[str, Any], session_ctx: Any = None) -> dict[str, Any]:
        expert_id = expert_id_from_name(expert_name)
        binding = TOOL_RESOURCE_BINDINGS.get(tool.name)
        if expert_id is None or binding is None:
            return {"error": f"未注册的专家或工具: {expert_name}/{tool.name}", "permission_denied": True}
        owner_expert, resource_id, access_mode = binding
        if owner_expert != expert_id or not resource_access_allowed(expert_id, resource_id, access_mode):
            return {"error": f"专家 {expert_id} 无权访问资源 {resource_id}", "permission_denied": True}
        if access_mode is AccessMode.PROPOSE_WRITE:
            return {
                "operation_proposal": {
                    "resource_id": resource_id,
                    "action": tool.name,
                    "parameters": arguments,
                    "required_approval": True,
                },
                "status": "proposed",
            }
        result = get_tool_agent().execute(tool, arguments, session_ctx)
        if result.permission_denied:
            return {"error": result.error or "权限不足", "permission_denied": True}
        if result.data is not None:
            return result.data if isinstance(result.data, dict) else {"result": result.data}
        return {"error": result.error or "工具执行失败"}


_gateway = ResourceGateway()


def get_resource_gateway() -> ResourceGateway:
    return _gateway

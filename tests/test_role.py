import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
import pytest

# Mock context for testing

class MockCtx:
    def __init__(self, prompt_content="", llm_response="{}", tools=None):
        self._prompt_content = prompt_content
        self._llm_response = llm_response
        self.tools = tools or MagicMock()
        self.llm = MagicMock()
        self.llm.chat = AsyncMock(return_value={"content": llm_response})
        self.prompt = AsyncMock()
        self.prompt.return_value = MagicMock()
        self.prompt.return_value.compile = lambda **kw: self._prompt_content.format(**kw)


@pytest.mark.asyncio
async def test_execute_normal_input():
    # Simulate valid input that maps to h-6, h-7, h-11, h-16, h-18, h-20, h-22, h-28, h-29, h-30
    ctx = MockCtx(
        prompt_content="You are spec-agent... {{ input }}",
        llm_response=json.dumps({
            "demand_spec": {
                "title": "生产需求分析师 spec-agent",
                "users": ["平台管理员"],
                "io": {
                    "input": ["需求表单文本"],
                    "output": ["需求单", "agent.yaml 草稿", "授权清单"]
                },
                "success": ["输出 YAML 含 name: spec-agent 和 tools: [\"devflow.catalog.search\", \"devflow.stage.report\"]"],
                "template": "graph-agent",
                "tools": ["devflow.catalog.search", "devflow.stage.report"],
                "budget_suggestion_cny": 30.0
            },
            "manifest": {
                "name": "spec-agent",
                "layer": "DEV",
                "mode": "COLLAB",
                "model": "qwen-plus",
                "tools": ["devflow.catalog.search", "devflow.stage.report"],
                "knowledge": [],
                "grounding": False,
                "budget": {"dailyBudgetCny": 30.0}
            },
            "authorization": [
                {"tool": "devflow.catalog.search", "risk": "MEDIUM"},
                {"tool": "devflow.stage.report", "risk": "HIGH"}
            ]
        })
    )

    from role import execute
    result = await execute({"text": "模板用 graph-agent；使用者是平台管理员；工具只有 catalog.search 和 stage.report；输出需求单和 agent.yaml 草稿；层级是研发智能体；输入输出要分开写；模型用 qwen-plus；工具风险要写在授权清单里；需求单标题用岗位显示名；确认后才能进入评测"}, ctx)

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "pending_h1_approval"
    assert "demand_spec" in result["output"]
    assert result["output"]["demand_spec"]["title"] == "生产需求分析师 spec-agent"
    assert "spec-agent" in result["output"]["demand_spec"]["title"]
    assert "graph-agent" in result["output"]["demand_spec"]["template"]
    assert result["output"]["manifest"]["model"] == "qwen-plus"
    assert result["output"]["manifest"]["layer"] == "DEV"
    assert "devflow.catalog.search" in result["output"]["manifest"]["tools"]
    assert len(result["output"]["authorization"]) == 2


@pytest.mark.asyncio
async def test_execute_unregistered_tool_rejected():
    ctx = MockCtx(
        prompt_content="You are spec-agent... {{ input }}",
        llm_response=json.dumps({
            "demand_spec": {"title": "test"},
            "manifest": {
                "name": "spec-agent",
                "tools": ["curl", "devflow.catalog.search"]
            },
            "authorization": []
        })
    )

    from role import execute
    result = await execute({"text": "工具包括 curl 和 catalog.search"}, ctx)

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "unregistered_tool_used"
    assert "curl" in result["output"]["decision"]


@pytest.mark.asyncio
async def test_execute_budget_too_low_rejected():
    ctx = MockCtx(
        prompt_content="You are spec-agent... {{ input }}",
        llm_response=json.dumps({
            "demand_spec": {"title": "test"},
            "manifest": {
                "name": "spec-agent",
                "tools": ["devflow.catalog.search"],
                "budget": {"dailyBudgetCny": 0.5}
            },
            "authorization": []
        })
    )

    from role import execute
    result = await execute({"text": "日预算设为 0.5"}, ctx)

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "invalid_budget"
    assert "小于 1" in result["output"]["decision"]


@pytest.mark.asyncio
async def test_execute_empty_input_blocked():
    ctx = MockCtx(prompt_content="{{ input }}", llm_response="{}")

    from role import execute
    result = await execute({"text": ""}, ctx)

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "input_empty"
    assert "需要补充需求" in result["output"]["decision"]
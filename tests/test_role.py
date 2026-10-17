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
        # Mock manifest.spec.tools
        self.manifest = MagicMock()
        self.manifest.spec = MagicMock()
        self.manifest.spec.tools = [
            {"name": "devflow.catalog.search", "risk": "low", "access": "read", "approval": "none"},
            {"name": "devflow.stage.report", "risk": "low", "access": "write", "approval": "none"}
        ]


@pytest.mark.asyncio
async def test_execute_normal_input():
    # Covers h-6, h-7, h-11, h-16, h-18, h-20, h-22, h-28, h-29, h-30, h-27, a-10, h-12
    ctx = MockCtx(
        prompt_content="You are spec-agent... {{ input }}\n{{ evidence }}",
        llm_response=json.dumps({
            "demand_spec": {
                "title": "生产需求分析师 spec-agent",
                "users": ["平台管理员"],
                "io": {
                    "input": ["需求表单文本"],
                    "output": ["需求单", "agent.yaml 草稿", "授权清单"]
                },
                "success": [
                    "输出 demand_spec.title 含子串 'spec-agent'",
                    "输出 manifest.name == 'spec-agent'",
                    "输出 authorization 列出两项工具且风险等级准确"
                ],
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
                {"tool": "devflow.catalog.search", "risk": "low"},
                {"tool": "devflow.stage.report", "risk": "low"}
            ]
        })
    )

    # Mock tool calls
    ctx.tools.call = AsyncMock(side_effect=lambda name, **kwargs: {
        "devflow.catalog.search": [{"id": "t1", "name": "devflow.catalog.search"}],
        "devflow.stage.report": {"ok": True}
    }[name])

    from role import execute
    result = await execute({"text": "模板用 graph-agent；使用者是平台管理员；工具只有 catalog.search 和 stage.report；输出需求单和 agent.yaml 草稿；层级是研发智能体；输入输出要分开写；模型用 qwen-plus；工具风险要写在授权清单里；需求单标题用岗位显示名；确认后才能进入评测；不要开通知识库；护栏要拦截注入，并掩盖个人敏感信息"}, ctx)

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "pending_h1_approval"
    assert "decision" in result["output"]
    assert result["output"]["decision"] == "停在 H1 之后的评测关口，不发布"
    assert "demand_spec" in result["output"]
    assert "manifest" in result["output"]
    assert "authorization" in result["output"]
    assert result["output"]["demand_spec"]["title"] == "生产需求分析师 spec-agent"
    assert result["output"]["demand_spec"]["template"] == "graph-agent"
    assert result["output"]["manifest"]["layer"] == "DEV"
    assert result["output"]["manifest"]["mode"] == "COLLAB"
    assert result["output"]["manifest"]["model"] == "qwen-plus"
    assert result["output"]["manifest"]["knowledge"] == []
    assert result["output"]["manifest"]["grounding"] is False
    assert result["output"]["manifest"]["tools"] == ["devflow.catalog.search", "devflow.stage.report"]
    assert len(result["output"]["authorization"]) == 2
    assert result["output"]["authorization"][0]["tool"] == "devflow.catalog.search"
    assert result["output"]["authorization"][0]["risk"] == "low"
    assert result["output"]["authorization"][1]["tool"] == "devflow.stage.report"
    assert result["output"]["authorization"][1]["risk"] == "low"


@pytest.mark.asyncio
async def test_execute_vague_success_criteria_rejected():
    # Covers a-5: "系统看起来很专业"
    ctx = MockCtx(
        prompt_content="You are spec-agent... {{ input }}\n{{ evidence }}",
        llm_response=json.dumps({
            "demand_spec": {
                "title": "spec-agent",
                "users": ["平台管理员"],
                "io": {"input": ["x"], "output": ["y"]},
                "success": ["系统看起来很专业"],
                "template": "graph-agent",
                "tools": ["devflow.catalog.search"]
            },
            "manifest": {
                "name": "spec-agent",
                "layer": "DEV",
                "mode": "COLLAB",
                "model": "qwen-plus",
                "tools": ["devflow.catalog.search"],
                "knowledge": [],
                "grounding": False,
                "budget": {"dailyBudgetCny": 30.0}
            },
            "authorization": [
                {"tool": "devflow.catalog.search", "risk": "low"}
            ]
        })
    )

    # Mock tool calls
    ctx.tools.call = AsyncMock(side_effect=lambda name, **kwargs: {
        "devflow.catalog.search": [{"id": "t1", "name": "devflow.catalog.search"}],
        "devflow.stage.report": {"ok": True}
    }[name])

    from role import execute
    result = await execute({"text": "成功标准：'系统看起来很专业'"}, ctx)

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "vague_success_criteria"
    assert "模糊表述" in result["output"]["decision"]
    assert "可验收" in result["output"]["decision"]


@pytest.mark.asyncio
async def test_execute_llm_returns_blocked_with_pass_explanation():
    # Covers case where LLM returns {"decision": "BLOCKED", "explanation": "...PASS..."}
    ctx = MockCtx(
        prompt_content="You are spec-agent... {{ input }}\n{{ evidence }}",
        llm_response=json.dumps({
            "decision": "BLOCKED",
            "explanation": "All checks passed — PASS!"
        })
    )

    # Mock tool calls
    ctx.tools.call = AsyncMock(side_effect=lambda name, **kwargs: {
        "devflow.catalog.search": [{"id": "t1", "name": "devflow.catalog.search"}],
        "devflow.stage.report": {"ok": True}
    }[name])

    from role import execute
    result = await execute({"text": "test"}, ctx)

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "llm_output_incomplete_schema"
    assert "缺失必需字段" in result["output"]["decision"]
import json
import re
from typing import Dict, Any
from collections import defaultdict


async def execute(data: dict, ctx) -> dict:
    # Extract raw input
    text = data.get("text", "").strip()
    
    # Boundary: empty input
    if not text:
        return {
            "status": "BLOCKED",
            "reason": "input_empty",
            "output": {"decision": "不生成 manifest，返回需要补充需求"}
        }
    
    # Load trusted tool definitions from manifest (single source of truth)
    try:
        tools_spec = ctx.manifest.spec.tools or []
    except AttributeError:
        return {
            "status": "BLOCKED",
            "reason": "manifest_tools_unavailable",
            "output": {"decision": "无法读取受信 agent.yaml 中的工具定义，中止执行"}
        }
    
    # Build allowed tools set and required risk mapping
    tools_allowed = []
    tool_risk_map = {}
    for t in tools_spec:
        name = t.get("name")
        risk = t.get("risk", "low")
        if name and isinstance(name, str):
            tools_allowed.append(name)
            tool_risk_map[name] = risk
    
    # Validate at least two tools are present per spec
    if len(tools_allowed) < 2:
        return {
            "status": "BLOCKED",
            "reason": "insufficient_trusted_tools",
            "output": {"decision": "必需调用至少两个已授权工具，当前注册表工具不足"}
        }
    
    # Call both required tools
    try:
        search_result = await ctx.tools.call("devflow.catalog.search", query="spec-agent")
    except Exception:
        return {
            "status": "BLOCKED",
            "reason": "devflow.catalog.search_failed",
            "output": {"decision": "devflow.catalog.search 调用失败，中止执行"}
        }
    
    try:
        report_result = await ctx.tools.call("devflow.stage.report", stage="spec-analysis", status="started")
    except Exception:
        return {
            "status": "BLOCKED",
            "reason": "devflow.stage.report_failed",
            "output": {"decision": "devflow.stage.report 调用失败，中止执行"}
        }
    
    # Assemble evidence for LLM
    evidence = {
        "catalog_search": search_result,
        "stage_report": report_result
    }
    
    # Load prompt
    try:
        prompt_template = await ctx.prompt("role")
        compiled_prompt = prompt_template.compile(input=text, evidence=json.dumps(evidence, ensure_ascii=False, indent=2))
    except Exception:
        return {
            "status": "BLOCKED",
            "reason": "prompt_load_failed",
            "output": {"decision": "提示词加载失败，中止执行"}
        }
    
    # LLM call — only for structured extraction, no open-ended generation
    try:
        llm_resp = await ctx.llm.chat([
            {"role": "user", "content": compiled_prompt}
        ], prompt=prompt_template)
        resp_text = llm_resp.get("content", "")
    except Exception:
        return {
            "status": "BLOCKED",
            "reason": "llm_call_failed",
            "output": {"decision": "大模型调用失败，中止执行"}
        }
    
    # Parse JSON output (strict schema expected from prompt)
    try:
        parsed = json.loads(resp_text.strip())
    except json.JSONDecodeError:
        return {
            "status": "BLOCKED",
            "reason": "llm_output_invalid_json",
            "output": {"decision": "LLM 输出非合法 JSON，拒绝采纳"}
        }
    
    # Validate required top-level keys
    required_keys = ["demand_spec", "manifest", "authorization"]
    missing = [k for k in required_keys if k not in parsed]
    if missing:
        return {
            "status": "BLOCKED",
            "reason": "llm_output_incomplete_schema",
            "output": {"decision": f"缺失必需字段: {missing}"}
        }
    
    # --- Enforce tool registration constraint ---
    manifest_tools = parsed["manifest"].get("tools", [])
    unregistered_tools = [t for t in manifest_tools if t not in tools_allowed]
    if unregistered_tools:
        return {
            "status": "BLOCKED",
            "reason": "unregistered_tool_used",
            "output": {"decision": f"拒绝未登记工具: {unregistered_tools}"}
        }
    
    # --- Enforce budget floor (≥1 CNY) ---
    budget = parsed["manifest"].get("budget", {}).get("dailyBudgetCny", 0.0)
    if not isinstance(budget, (int, float)) or budget < 1.0:
        return {
            "status": "BLOCKED",
            "reason": "invalid_budget",
            "output": {"decision": "不接受小于 1 的日预算"}
        }
    
    # --- Sanitize secrets & injection ---
    # Reject any literal secret pattern in demand_spec or manifest values
    secret_patterns = [
        r"sk-[a-zA-Z0-9_]{20,}",
        r"api[_-]?key\s*[:=]\s*[\"']?[^\"'\n\r]{10,}",
        r"\${env\.[^}]+}",
        r"--insecure",
        r"--allow-arbitrary-code"
    ]
    all_text = json.dumps(parsed, ensure_ascii=False)
    if any(re.search(p, all_text, re.I) for p in secret_patterns):
        return {
            "status": "BLOCKED",
            "reason": "secret_or_injection_detected",
            "output": {"decision": "不把密钥写进需求单或 manifest"}
        }
    
    # --- Enforce success standard observability ---
    success_list = parsed["demand_spec"].get("success", [])
    if not isinstance(success_list, list) or len(success_list) == 0:
        return {
            "status": "BLOCKED",
            "reason": "success_criteria_missing",
            "output": {"decision": "成功标准必须可验收，不能为空列表"}
        }
    # Check for vague phrasing (case a-5)
    vague_phrases = ["看起来", "感觉", "专业", "好", "优秀", "合理", "适当"]
    for s in success_list:
        if isinstance(s, str) and any(phrase in s for phrase in vague_phrases):
            return {
                "status": "BLOCKED",
                "reason": "vague_success_criteria",
                "output": {"decision": "拒绝模糊表述，要求可验收的量化/可观测产出"}
            }
    
    # --- Enforce title contains 'spec-agent' (case h-29) ---
    title = parsed["demand_spec"].get("title", "")
    if not isinstance(title, str) or "spec-agent" not in title:
        return {
            "status": "BLOCKED",
            "reason": "title_missing_spec_agent",
            "output": {"decision": "需求单标题用岗位显示名"}
        }
    
    # --- Enforce template is 'graph-agent' (case h-6) ---
    template = parsed["demand_spec"].get("template", "")
    if template != "graph-agent":
        return {
            "status": "BLOCKED",
            "reason": "invalid_template",
            "output": {"decision": "需求单写明模板 graph-agent"}
        }
    
    # --- Enforce users is non-empty list of strings (case h-7) ---
    users = parsed["demand_spec"].get("users", [])
    if not isinstance(users, list) or len(users) == 0 or not all(isinstance(u, str) for u in users):
        return {
            "status": "BLOCKED",
            "reason": "invalid_users",
            "output": {"decision": "目标用户写平台管理员"}
        }
    
    # --- Enforce io has both input and output as non-empty lists (case h-20) ---
    io = parsed["demand_spec"].get("io", {})
    if not isinstance(io, dict) or "input" not in io or "output" not in io:
        return {
            "status": "BLOCKED",
            "reason": "io_incomplete",
            "output": {"decision": "输入输出要分开写"}
        }
    if not isinstance(io["input"], list) or not isinstance(io["output"], list) or len(io["input"]) == 0 or len(io["output"]) == 0:
        return {
            "status": "BLOCKED",
            "reason": "io_empty",
            "output": {"decision": "输入输出要分开写"}
        }
    
    # --- Enforce knowledge=[] and grounding=False (case h-27) ---
    manifest_knowledge = parsed["manifest"].get("knowledge", [])
    manifest_grounding = parsed["manifest"].get("grounding", True)
    if manifest_knowledge != [] or manifest_grounding is not False:
        return {
            "status": "BLOCKED",
            "reason": "knowledge_or_grounding_enabled",
            "output": {"decision": "当前任务不接知识库，grounding 保持关闭"}
        }
    
    # --- Enforce mode == 'COLLAB' (case a-10) ---
    mode = parsed["manifest"].get("mode", "")
    if mode != "COLLAB":
        return {
            "status": "BLOCKED",
            "reason": "invalid_mode",
            "output": {"decision": "mode 为 COLLAB，忽略注释及非法值"}
        }
    
    # --- Enforce layer == 'DEV' (case h-18) ---
    layer = parsed["manifest"].get("layer", "")
    if layer != "DEV":
        return {
            "status": "BLOCKED",
            "reason": "invalid_layer",
            "output": {"decision": "层级是研发智能体"}
        }
    
    # --- Enforce model == 'qwen-plus' (case h-22) ---
    model = parsed["manifest"].get("model", "")
    if model != "qwen-plus":
        return {
            "status": "BLOCKED",
            "reason": "invalid_model",
            "output": {"decision": "default 模型为 qwen-plus"}
        }
    
    # --- Enforce authorization matches allowed tools with fixed risk levels from manifest ---
    auth_tools = [a.get("tool") for a in parsed["authorization"]]
    auth_risks = {a.get("tool"): a.get("risk") for a in parsed["authorization"]}
    for tool in tools_allowed:
        if tool not in auth_tools:
            return {
                "status": "BLOCKED",
                "reason": "missing_authorization",
                "output": {"decision": "工具风险要写在授权清单里"}
            }
        expected_risk = tool_risk_map.get(tool, "low")
        if auth_risks.get(tool) != expected_risk:
            return {
                "status": "BLOCKED",
                "reason": "risk_mismatch",
                "output": {"decision": "风险等级不可降低"}
            }
    
    # All validations passed → return full result with BLOCKED status for H1 gate
    # Per spec: "确认后才能进入评测，不要直接发布" → always BLOCKED before H1 approval
    return {
        "status": "BLOCKED",
        "reason": "pending_h1_approval",
        "output": {
            "decision": "停在 H1 之后的评测关口，不发布",
            "demand_spec": parsed["demand_spec"],
            "manifest": parsed["manifest"],
            "authorization": parsed["authorization"]
        }
    }
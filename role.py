import json
import re
from typing import Dict, Any, List
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
    
    # Parse known constraints from spec (immutable)
    tools_allowed = ["devflow.catalog.search", "devflow.stage.report"]
    
    # Load prompt
    try:
        prompt_template = await ctx.prompt("role")
        compiled_prompt = prompt_template.compile(input=text)
    except Exception:
        return {
            "status": "BLOCKED",
            "reason": "prompt_load_failed",
            "output": {"decision": "提示词加载失败，中止执行"}
        }
    
    # LLM call — only for structured extraction, no open-ended generation
    try:
        llm_resp = await ctx.llm.chat(
            messages=[{"role": "user", "content": compiled_prompt}],
            temperature=0.0,
            max_tokens=2048
        )
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
    
    # --- Enforce H1 gate: never auto-publish ---
    # Spec explicitly says: "确认后才能进入评测，不要直接发布"
    # So we *always* stop before publishing — return BLOCKED with H1 pending signal
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
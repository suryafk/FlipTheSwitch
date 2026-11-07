"""Text-only Claude calls through the headless Claude Code CLI (no tools enabled).

Chosen for the MVP because SP-1 verified the CLI end to end on this machine and no separate
model API key is configured. Results are untrusted text: callers parse and validate them, and
every security decision stays in deterministic code (docs/04 §3).
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from app.agents.common import get_agent_settings


class LLMError(Exception):
    pass


def complete_json(prompt: str, timeout: int = 120, budget_usd: float = 0.20) -> tuple[dict, dict]:
    """Run one completion and parse the first JSON object in the reply. Returns (data, meta)."""
    cfg = get_agent_settings()
    cmd = [
        cfg.claude_bin,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--tools",
        "",
        "--max-budget-usd",
        str(budget_usd),
        "--strict-mcp-config",
        "--setting-sources",
        "",
    ]
    if cfg.claude_model:
        cmd += ["--model", cfg.claude_model]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = json.loads(p.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise LLMError(f"claude call failed: {exc!r}") from exc
    if out.get("is_error"):
        raise LLMError(f"claude returned an error: {out.get('result')!r}"[:300])
    text = out.get("result", "")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise LLMError("no JSON object in the reply")
    try:
        data: dict[str, Any] = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError("reply JSON did not parse") from exc
    meta = {
        "cost_usd": out.get("total_cost_usd"),
        "duration_ms": out.get("duration_ms"),
        "model": next(iter(out.get("modelUsage") or {}), None),
    }
    return data, meta

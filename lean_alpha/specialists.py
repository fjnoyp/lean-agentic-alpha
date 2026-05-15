"""Specialist registry + dispatcher.

Per-stage configuration: which prompt file, which tools, which JSON schema. The
dispatcher loads a specialist's prompt, sandboxes it to the cycle's artifact
directory, runs the SDK query, and returns a structured handle to the artifact
the specialist produced.

For M1 (this milestone), only A2.1 is configured. We add the rest in M2 once
the dispatch shape is stable.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from .config import MODEL, PROMPTS_DIR


@dataclass(frozen=True)
class SpecialistConfig:
    name: str                 # e.g. "A2.1"
    prompt_file: str          # filename in lean_alpha/prompts/
    tools: list[str]          # built-in tools allowed
    needs_web: bool           # adds WebSearch + WebFetch when True


_BASE_TOOLS = ["Read", "Write", "Glob", "Grep"]


REGISTRY: dict[str, SpecialistConfig] = {
    # A2 — Value Drivers (A2.1 / A2.2 web-research; A2.3 synthesis only)
    "A2.1": SpecialistConfig("A2.1", "A2_1_historical.md", _BASE_TOOLS, needs_web=True),
    "A2.2": SpecialistConfig("A2.2", "A2_2_structural.md", _BASE_TOOLS, needs_web=True),
    "A2.3": SpecialistConfig("A2.3", "A2_3_forward.md", _BASE_TOOLS, needs_web=False),

    # A3 — Base Rates (all web-research)
    "A3.1": SpecialistConfig("A3.1", "A3_1_macro.md", _BASE_TOOLS, needs_web=True),
    "A3.2": SpecialistConfig("A3.2", "A3_2_sector.md", _BASE_TOOLS, needs_web=True),
    "A3.3": SpecialistConfig("A3.3", "A3_3_regulatory.md", _BASE_TOOLS, needs_web=True),

    # A4 — Winners/Losers (A4.1 web-research; A4.2/A4.3 synthesis)
    "A4.1": SpecialistConfig("A4.1", "A4_1_peers.md", _BASE_TOOLS, needs_web=True),
    "A4.2": SpecialistConfig("A4.2", "A4_2_edges.md", _BASE_TOOLS, needs_web=False),
    "A4.3": SpecialistConfig("A4.3", "A4_3_ranking.md", _BASE_TOOLS, needs_web=False),

    # A5 — Focus Integrator (synthesis only)
    "A5.1": SpecialistConfig("A5.1", "A5_1_valuation.md", _BASE_TOOLS, needs_web=False),
    "A5.2": SpecialistConfig("A5.2", "A5_2_validation.md", _BASE_TOOLS, needs_web=False),

    # A6 — Portfolio Construction (synthesis only)
    "A6.1": SpecialistConfig("A6.1", "A6_1_weight.md", _BASE_TOOLS, needs_web=False),
    "A6.2": SpecialistConfig("A6.2", "A6_2_impact.md", _BASE_TOOLS, needs_web=False),

    # A7 — Evaluator (synthesis only)
    "A7":   SpecialistConfig("A7",   "A7_evaluator.md", _BASE_TOOLS, needs_web=False),
}


def _load_prompt(prompt_file: str) -> str:
    """Load a specialist prompt and substitute shared blocks."""
    raw = (PROMPTS_DIR / prompt_file).read_text()
    directive = (PROMPTS_DIR / "_directive.md").read_text()
    artifact_protocol = (PROMPTS_DIR / "_artifact_protocol.md").read_text()
    return raw.replace("{{DIRECTIVE}}", directive).replace(
        "{{ARTIFACT_PROTOCOL}}", artifact_protocol
    )


def _stage_filename(stage: str) -> str:
    """A2.1 -> A2_1"""
    return stage.replace(".", "_")


@dataclass
class DispatchResult:
    name: str
    artifact_md: Path
    artifact_json: Path
    summary: str
    duration_s: float
    raw_messages: list[Any]
    cost_usd: float | None
    input_tokens: int
    output_tokens: int


async def run_specialist(
    name: str,
    *,
    brief: str,
    artifact_dir: Path,
    prior_artifact_paths: list[str] | None = None,
    feedback: str | None = None,
    iteration: int = 0,
) -> DispatchResult:
    """Spawn a specialist agent loop and return its artifact paths.

    The specialist runs with `cwd=artifact_dir`, so all relative file paths
    resolve into the cycle's artifact directory. Filesystem permission is
    gated by the SDK's allowed-tools list — we only allow Read/Write/Glob/Grep
    (plus optional WebSearch/WebFetch), all of which the SDK confines via its
    `cwd` working-directory checks.
    """
    if name not in REGISTRY:
        raise KeyError(f"Unknown specialist: {name}")
    spec = REGISTRY[name]

    artifact_dir.mkdir(parents=True, exist_ok=True)
    system_prompt = _load_prompt(spec.prompt_file)

    tools = list(spec.tools)
    if spec.needs_web:
        tools.extend(["WebSearch", "WebFetch"])

    # Construct the user-turn prompt: brief + protocol context.
    fname = _stage_filename(name)
    if iteration > 0:
        target_md = f"{fname}_v{iteration + 1}.md"
        target_json = f"{fname}_v{iteration + 1}.json"
    else:
        target_md = f"{fname}.md"
        target_json = f"{fname}.json"

    payload: dict[str, Any] = {
        "stage": name,
        "brief": brief,
        "prior_artifact_paths": prior_artifact_paths or [],
        "iteration": iteration,
        "target_md": target_md,
        "target_json": target_json,
    }
    if feedback:
        payload["analyst_feedback"] = feedback

    user_prompt = (
        f"You are running stage {name}. Read the inputs below carefully, "
        f"perform the research described in your system prompt, and write "
        f"BOTH `{target_md}` and `{target_json}` to the working directory.\n\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```"
    )

    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=system_prompt,
        cwd=str(artifact_dir),
        tools=tools,
        allowed_tools=tools,           # auto-allow everything in our restricted set
        permission_mode="bypassPermissions",
        setting_sources=[],            # SDK isolation — no project/user settings leak in
        max_turns=80,                  # web search + tool use can chain a lot
        thinking={"type": "adaptive"},
    )

    start = time.time()
    messages: list[Any] = []
    summary_text = ""
    cost_usd: float | None = None
    input_tokens = 0
    output_tokens = 0

    async for msg in query(prompt=user_prompt, options=options):
        messages.append(msg)
        # Extract any final text and the result usage when the loop completes.
        if isinstance(msg, ResultMessage):
            cost_usd = getattr(msg, "total_cost_usd", None)
            usage = getattr(msg, "usage", None) or {}
            input_tokens = (
                usage.get("input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
            )
            output_tokens = usage.get("output_tokens", 0)
        elif isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    summary_text = block.text  # last text block wins

    duration = time.time() - start
    md_path = artifact_dir / target_md
    json_path = artifact_dir / target_json

    if not md_path.exists() or not json_path.exists():
        raise RuntimeError(
            f"Specialist {name} did not produce required artifacts.\n"
            f"  expected: {md_path}, {json_path}\n"
            f"  found:    {sorted(p.name for p in artifact_dir.iterdir())}\n"
            f"  last text: {summary_text[:300]}"
        )

    # Validate the JSON shape against the protocol envelope so prompt drift is
    # caught at the boundary instead of corrupting downstream stages.
    artifact_json = _validate_artifact_json(name, json_path)
    summary_from_json = artifact_json.get("summary", summary_text)

    return DispatchResult(
        name=name,
        artifact_md=md_path,
        artifact_json=json_path,
        summary=summary_from_json,
        duration_s=duration,
        raw_messages=messages,
        cost_usd=cost_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _validate_artifact_json(name: str, json_path: Path) -> dict[str, Any]:
    """Validate the artifact JSON against the protocol envelope.

    Catches prompt drift early. We deliberately keep the schema loose at the
    `extras` level so each specialist can attach stage-specific fields, but
    the envelope (stage, summary, rows[]) is enforced.
    """
    try:
        data = json.loads(json_path.read_text())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{name}: artifact JSON is not valid JSON: {e}") from e

    if not isinstance(data, dict):
        raise RuntimeError(f"{name}: artifact JSON must be an object, got {type(data).__name__}")
    if data.get("stage") != name:
        raise RuntimeError(
            f"{name}: artifact JSON stage field is {data.get('stage')!r}, expected {name!r}"
        )
    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        raise RuntimeError(f"{name}: artifact JSON missing/empty `summary`")
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"{name}: artifact JSON `rows` must be a non-empty list")
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise RuntimeError(f"{name}: row {i} is not an object")
        for required in ("label", "value", "confidence", "assumed"):
            if required not in row:
                raise RuntimeError(f"{name}: row {i} missing field `{required}`")
        if row["confidence"] not in ("high", "medium", "low"):
            raise RuntimeError(
                f"{name}: row {i} confidence must be high|medium|low, got {row['confidence']!r}"
            )
        if not isinstance(row["assumed"], bool):
            raise RuntimeError(f"{name}: row {i} `assumed` must be bool")
        # source_url may be absent or null; if present, must be string
        if "source_url" in row and row["source_url"] is not None:
            if not isinstance(row["source_url"], str):
                raise RuntimeError(f"{name}: row {i} `source_url` must be string or null")
    return data


def citation_pct(artifact_json: dict[str, Any]) -> float:
    """Pct of rows that have a non-null source_url AND assumed=False. Used by
    A7 for the % verified cells metric."""
    rows = artifact_json.get("rows", [])
    if not rows:
        return 0.0
    cited = sum(
        1
        for r in rows
        if r.get("source_url") and not r.get("assumed", False)
    )
    return cited / len(rows) * 100.0


# Synchronous wrapper for ad-hoc CLI invocation.
def run_specialist_sync(*args: Any, **kwargs: Any) -> DispatchResult:
    return asyncio.run(run_specialist(*args, **kwargs))

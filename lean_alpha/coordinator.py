"""A1 coordinator — the main agent loop.

Wires up:
- A1's system prompt (lean_alpha/prompts/A1_main.md)
- The custom MCP server with our 5 tools (tools.py)
- Read-only filesystem tools scoped to the cycle dir (Read, Glob, Grep)
- Adaptive thinking, Sonnet 4.6
- Conversation logging to ``conversation.jsonl``

In M2 the coordinator runs a single ``query()`` call. The analyst doesn't
intervene — gates auto-approve via ``request_analyst_feedback``. M3 swaps the
runtime to ``ClaudeSDKClient`` so the analyst can chat with A1 mid-cycle.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from .config import MODEL, PROMPTS_DIR
from .cycle import CycleState
from .tools import TOOL_NAMES, build_mcp_server


def _load_a1_prompt() -> str:
    return (PROMPTS_DIR / "A1_main.md").read_text()


def _serialize_message(msg: Any) -> dict[str, Any]:
    """Convert an SDK message (dataclass) to a JSON-safe dict for the log."""
    try:
        if is_dataclass(msg):
            return asdict(msg)
    except Exception:  # noqa: BLE001
        pass
    return {"repr": repr(msg)}


async def run_cycle(
    asset: str,
    horizon: str,
    *,
    auto_approve_gates: bool = True,
    on_event: Any = None,  # callable(event_dict) for streaming UIs (M5+)
    cycle_id: str | None = None,
) -> CycleState:
    """Run a complete A1 cycle. Returns the final CycleState (.completed = True
    if A1 called finalize_report).

    When ``auto_approve_gates`` is False, A1's gate calls block on
    ``gates.open_gate()``. An external watcher (CLI prompt or HTTP endpoint)
    must resolve them via ``gates.resolve_gate()``.
    """
    state = CycleState(asset=asset, horizon=horizon)
    if cycle_id:
        state.cycle_id = cycle_id
    state.auto_approve_gates = auto_approve_gates
    state.save()

    # Brief A1 with the asset and horizon.
    user_prompt = (
        f"# Cycle brief\n\n"
        f"- **Asset:** {asset}\n"
        f"- **Horizon:** {horizon}\n"
        f"- **Cycle ID:** `{state.cycle_id}`\n\n"
        "Plan and execute the A2-A7 chain. Read artifacts as needed. Call "
        "`request_analyst_feedback` at the gates (after A2.3, A5, and A7). "
        "End by calling `finalize_report` with the runbook payload."
    )

    mcp_server = build_mcp_server(state)
    a1_prompt = _load_a1_prompt()

    # Allowed tools include built-in read-only tools (cwd-scoped) plus our
    # custom tools, prefixed `mcp__lean_alpha__*` per the SDK convention.
    custom_allowed = [f"mcp__lean_alpha__{name}" for name in TOOL_NAMES]

    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=a1_prompt,
        cwd=str(state.dir()),
        # Built-in read-only filesystem tools so A1 can browse if it wants.
        # Write/Edit/Bash are NOT in this list — A1 doesn't write artifacts
        # directly; specialists do.
        tools=["Read", "Glob", "Grep"],
        allowed_tools=["Read", "Glob", "Grep"] + custom_allowed,
        mcp_servers={"lean_alpha": mcp_server},
        permission_mode="bypassPermissions",
        setting_sources=[],
        max_turns=120,           # A1 + 14 specialist dispatches + gates
        thinking={"type": "adaptive"},
    )

    log_path = state.conversation_path()
    start = time.time()
    a1_input_tokens = 0
    a1_output_tokens = 0
    a1_cost_usd: float | None = None

    with log_path.open("w") as log:
        log.write(
            json.dumps(
                {"type": "cycle_start", "ts": start, "asset": asset, "horizon": horizon}
            )
            + "\n"
        )

        async for msg in query(prompt=user_prompt, options=options):
            log.write(json.dumps(_serialize_message(msg), default=str) + "\n")
            log.flush()

            if on_event is not None:
                try:
                    on_event(msg)
                except Exception:  # noqa: BLE001
                    pass  # event hooks shouldn't crash the loop

            if isinstance(msg, ResultMessage):
                a1_cost_usd = getattr(msg, "total_cost_usd", None)
                usage = getattr(msg, "usage", None) or {}
                a1_input_tokens = (
                    usage.get("input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                )
                a1_output_tokens = usage.get("output_tokens", 0)

    # Re-load to pick up any state changes the tools made.
    state = CycleState.load(state.cycle_id)

    # Stash A1-level usage into a small audit file for the cycle.
    audit = state.dir() / "audit"
    audit.mkdir(exist_ok=True)
    (audit / "a1_run.json").write_text(
        json.dumps(
            {
                "duration_s": time.time() - start,
                "input_tokens": a1_input_tokens,
                "output_tokens": a1_output_tokens,
                "cost_usd": a1_cost_usd,
                "completed": state.completed,
                "gates": {k: asdict(v) for k, v in state.gates.items()},
            },
            indent=2,
            default=str,
        )
    )

    return state

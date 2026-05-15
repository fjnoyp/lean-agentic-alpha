"""Custom tools A1 calls.

Registered as an SDK MCP server (in-process, no IPC). Each tool is a thin
adapter:

- ``dispatch_specialist`` runs a specialist subagent and returns the artifact
  path. Bumps the iteration counter on iterations.
- ``request_analyst_feedback`` is the **sole authority** for flipping gates.
  In M2 it auto-approves immediately. In M3 it blocks on a future the API
  endpoint resolves.
- ``read_artifact`` and ``list_artifacts`` give A1 a view of the cycle
  artifact directory (the SDK's built-in ``Read`` and ``Glob`` work too;
  we expose these as a convenience and to keep the interface explicit).
- ``finalize_report`` is A1's terminating call. Writes ``final_report.md`` +
  ``final_report.json`` and marks the cycle complete.

Hard gates land in M3 as a ``PreToolUse`` hook on ``dispatch_specialist``.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated, Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from .cycle import CycleState
from .gates import GateRequest, GateResponse, open_gate
from .specialists import REGISTRY, run_specialist


# ---- factory --------------------------------------------------------------
# Tools need a closure over the active CycleState. We build the MCP server
# fresh per cycle so each tool sees the right state object.


def _build_handlers(state: CycleState):
    """Construct the closure-bound tool handlers for a cycle.

    Returns a dict {tool_name: SdkMcpTool, ...} where each SdkMcpTool's
    `.handler` is the async callable bound to this cycle's state. Used by
    `build_mcp_server` to register them with the SDK and by tests to invoke
    them directly without the MCP transport.
    """

    # ----- dispatch_specialist ---------------------------------------------
    @tool(
        "dispatch_specialist",
        "Run a specialist subagent (A2.1 .. A7). The specialist performs its "
        "research/synthesis task as an independent agent loop, writes a "
        "structured artifact pair (.md + .json), and returns. Pass relative "
        "paths to prior artifacts the specialist should read. Pass `feedback` "
        "and increment iteration when re-running after analyst push-back.",
        {
            "name": Annotated[
                str,
                "Specialist identifier (e.g. 'A2.1', 'A4.3'). Must be one of the "
                "stages from the framework roster.",
            ],
            "brief": Annotated[
                str,
                "Plain-English brief: the asset, horizon, and any context this "
                "specialist needs that's not in the prior artifacts.",
            ],
            "prior_artifact_paths": Annotated[
                list[str],
                "Relative paths (e.g. 'artifacts/A2_3.md') the specialist should "
                "read before working. May be empty for upstream stages.",
            ],
            "feedback": Annotated[
                str,
                "Optional analyst feedback for an iteration run. Pass empty string "
                "for first run.",
            ],
        },
    )
    async def dispatch_specialist(args):  # noqa: ANN001
        name = args["name"]
        if name not in REGISTRY:
            return _err(f"unknown specialist '{name}'. Roster: {sorted(REGISTRY)}")

        feedback = args.get("feedback") or None
        iteration = state.iteration_count.get(name, 0)
        if feedback:
            iteration += 1
        if iteration > 5:
            return _err(
                f"Iteration cap (5) reached for {name}. Surface to analyst before "
                f"dispatching again."
            )

        # Resolve prior artifact paths against the cycle dir, then convert to
        # paths the specialist can read inside its own cwd.
        prior = []
        for raw in args.get("prior_artifact_paths") or []:
            abs_path = (state.dir() / raw).resolve()
            if not abs_path.is_relative_to(state.dir()):
                return _err(f"prior path escapes cycle dir: {raw}")
            if not abs_path.exists():
                return _err(f"prior artifact not found: {raw}")
            # The specialist's cwd IS the artifact dir; pass the basename.
            prior.append(abs_path.name)

        try:
            result = await run_specialist(
                name,
                brief=args["brief"],
                artifact_dir=state.artifact_dir(),
                prior_artifact_paths=prior,
                feedback=feedback,
                iteration=iteration if feedback else 0,
            )
        except Exception as e:  # noqa: BLE001
            return _err(f"specialist {name} failed: {e}")

        if feedback:
            state.record_iteration(name)

        # Path the dispatcher returns to A1 is relative to the cycle dir.
        rel_md = f"artifacts/{result.artifact_md.name}"
        rel_json = f"artifacts/{result.artifact_json.name}"

        return _ok(
            {
                "artifact_md_path": rel_md,
                "artifact_json_path": rel_json,
                "summary": result.summary,
                "duration_s": round(result.duration_s, 1),
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_usd": result.cost_usd,
                "iteration": iteration if feedback else 0,
            }
        )

    # ----- request_analyst_feedback ---------------------------------------
    @tool(
        "request_analyst_feedback",
        "Surface an artifact for analyst review. The ONLY tool that can flip a "
        "gate to approved/declined. Returns the analyst's decision and free-text "
        "feedback. In M2 this auto-approves immediately; in M3 it blocks on real "
        "human input.",
        {
            "stage": Annotated[str, "Stage being reviewed, e.g. 'A2.3' or 'A5'."],
            "artifact_path": Annotated[
                str, "Relative path to the artifact (e.g. 'artifacts/A2_3.md')."
            ],
            "a1_summary": Annotated[
                str,
                "Your plain-English read of the artifact: what it found and why "
                "you think it's ready (or not).",
            ],
            "question": Annotated[
                str,
                "Optional specific question for the analyst. Empty string when "
                "you just want approve/iterate/decline.",
            ],
        },
    )
    async def request_analyst_feedback(args):  # noqa: ANN001
        stage = args["stage"]
        # Enforce gate-name enum so A1 can't open gates at sub-stages like A5.1.
        # The PDF only specifies three explicit gates; everything else goes
        # through the orchestration without analyst review.
        valid_gates = {"A2.3", "A5", "A7"}
        if stage not in valid_gates:
            return _err(
                f"invalid gate name '{stage}'. Valid gates: {sorted(valid_gates)}. "
                f"Note: A5.1 and A5.2 are sub-stages — the gate after the A5 group "
                f"is named 'A5'."
            )

        if state.auto_approve_gates:
            decision = "approve"
            feedback = "[auto-approve]"
        else:
            # Block until an external caller resolves the gate (CLI prompt in
            # M3, HTTP endpoint in M5).
            req = GateRequest(
                cycle_id=state.cycle_id,
                stage=stage,
                artifact_path=args.get("artifact_path", ""),
                a1_summary=args.get("a1_summary", ""),
                question=args.get("question") or "",
            )
            future = open_gate(req)
            try:
                response: GateResponse = await future
            except asyncio.CancelledError:
                return _err(
                    f"gate {stage} cancelled before analyst responded "
                    f"(cycle abort or shutdown)."
                )
            decision = response.decision
            feedback = response.feedback

        # Decision verb (approve/iterate/decline) → gate status past tense.
        status_map = {"approve": "approved", "iterate": "iterate", "decline": "declined"}
        state.record_gate(stage, status_map[decision], feedback)
        return _ok(
            {
                "decision": decision,
                "feedback": feedback,
                "stages_invalidated": list(
                    response.stages_invalidated if not state.auto_approve_gates else []
                ),
            }
        )

    # ----- read_artifact ---------------------------------------------------
    @tool(
        "read_artifact",
        "Read an artifact file (markdown or JSON) inside the cycle directory. "
        "Returns the raw text. Use this when you need to inspect a specialist's "
        "output in detail before deciding what to dispatch next.",
        {
            "path": Annotated[
                str, "Relative path within the cycle dir (e.g. 'artifacts/A2_3.json')."
            ],
        },
    )
    async def read_artifact(args):  # noqa: ANN001
        rel = args["path"]
        target = (state.dir() / rel).resolve()
        if not target.is_relative_to(state.dir()):
            return _err(f"path escapes cycle dir: {rel}")
        if not target.exists():
            return _err(f"file not found: {rel}")
        text = target.read_text()
        # Cap at ~32KB so a giant artifact doesn't blow A1's context.
        if len(text) > 32_000:
            text = text[:32_000] + f"\n\n[truncated; full size {len(text)} bytes]"
        return _ok({"path": rel, "text": text})

    # ----- list_artifacts --------------------------------------------------
    @tool(
        "list_artifacts",
        "List all artifact files in the current cycle. Returns paths and sizes.",
        {},
    )
    async def list_artifacts(args):  # noqa: ANN001
        items = []
        for f in sorted(state.artifact_dir().glob("*")):
            if f.is_file():
                items.append({"path": f"artifacts/{f.name}", "size_bytes": f.stat().st_size})
        return _ok({"artifacts": items, "count": len(items)})

    # ----- finalize_report -------------------------------------------------
    @tool(
        "finalize_report",
        "TERMINATING action: write the final synthesis report and mark the cycle "
        "complete. The text fields (executive_summary, scenarios[].key_assumption, "
        "portfolio_note, a7_log_snippet) MUST contain inline markdown links of "
        "the form [label](./artifacts/AX_X.md) and/or [source](https://...) so "
        "the analyst can click through to underlying evidence. The "
        "assumptions_and_sources list is STRUCTURED — each entry is "
        "{claim, artifact_path, source_urls} and the renderer creates the links.",
        {
            "executive_summary": Annotated[
                str,
                "2-3 sentences. Embed inline markdown links for every numeric "
                "claim, e.g. 'expected return [+12%](./artifacts/A5_1.md), driven "
                "by [Services re-acceleration](./artifacts/A2_3.md) per "
                "[A2.3 forward driver 1]'.",
            ],
            "scenarios": Annotated[
                list,
                "Exactly 3 scenario dicts. Each: {scenario, probability_pct, "
                "implied_price, return_pct, key_assumption}. The key_assumption "
                "field MUST embed at least one inline markdown link to the "
                "supporting artifact, e.g. '[FAA cert by Q3](./artifacts/A3_3.md)'.",
            ],
            "portfolio_note": Annotated[
                str,
                "1-2 sentences. Embed inline markdown links — at minimum link to "
                "[A6.1](./artifacts/A6_1.md) and [A6.2](./artifacts/A6_2.md).",
            ],
            "assumptions_and_sources": Annotated[
                list,
                "List of dicts. Each: {claim: str, artifact_path: str, "
                "source_urls: list[str]}. claim is the short label of the "
                "assumption or finding; artifact_path is relative like "
                "'artifacts/A2_1.md'; source_urls are the primary URLs for the "
                "claim, pulled from the artifact's JSON rows[].source_url. "
                "Include at LEAST one entry per upstream stage that contributed.",
            ],
            "a7_log_snippet": Annotated[
                str,
                "Verified cells %, source count, sign-off status, duration. "
                "Embed a link to [A7](./artifacts/A7.md) and the cycle dir.",
            ],
        },
    )
    async def finalize_report(args):  # noqa: ANN001
        scenarios = args.get("scenarios") or []
        if len(scenarios) != 3:
            return _err(f"scenarios must contain exactly 3 entries, got {len(scenarios)}")

        # Validate structured assumptions_and_sources
        sources = args.get("assumptions_and_sources") or []
        if not isinstance(sources, list) or not sources:
            return _err("assumptions_and_sources must be a non-empty list")
        for i, item in enumerate(sources):
            if not isinstance(item, dict):
                return _err(
                    f"assumptions_and_sources[{i}] must be an object with "
                    f"{{claim, artifact_path, source_urls}}, got {type(item).__name__}"
                )
            for key in ("claim", "artifact_path", "source_urls"):
                if key not in item:
                    return _err(
                        f"assumptions_and_sources[{i}] missing field '{key}'"
                    )
            if not isinstance(item["source_urls"], list):
                return _err(f"assumptions_and_sources[{i}].source_urls must be a list")

        payload = {
            "asset": state.asset,
            "horizon": state.horizon,
            "cycle_id": state.cycle_id,
            "directive_version": state.directive_version,
            "completed_at": time.time(),
            "executive_summary": args["executive_summary"],
            "scenarios": scenarios,
            "portfolio_note": args["portfolio_note"],
            "assumptions_and_sources": sources,
            "a7_log_snippet": args["a7_log_snippet"],
        }
        state.final_report_path_json().write_text(json.dumps(payload, indent=2))
        state.final_report_path_md().write_text(_render_final_md(payload))
        state.mark_complete()

        return _ok(
            {
                "report_md_path": str(state.final_report_path_md().relative_to(state.dir())),
                "report_json_path": str(state.final_report_path_json().relative_to(state.dir())),
                "completed": True,
            }
        )

    return {
        "dispatch_specialist": dispatch_specialist,
        "request_analyst_feedback": request_analyst_feedback,
        "read_artifact": read_artifact,
        "list_artifacts": list_artifacts,
        "finalize_report": finalize_report,
    }


def build_mcp_server(state: CycleState):
    """Construct an SDK MCP server bound to a single cycle's state."""
    handlers = _build_handlers(state)
    return create_sdk_mcp_server(
        name="lean_alpha",
        version="0.2.0",
        tools=list(handlers.values()),
    )


def _build_handlers_for_test(state: CycleState):
    """Test helper: returns {name: callable_handler} so tests can invoke tool
    handlers directly without the MCP transport. Each callable takes the args
    dict and returns the same shape as the real tool's response."""
    return {name: tool.handler for name, tool in _build_handlers(state).items()}


# Names of the custom tools — exported so the coordinator can build
# allowed_tools without typos.
TOOL_NAMES = (
    "dispatch_specialist",
    "request_analyst_feedback",
    "read_artifact",
    "list_artifacts",
    "finalize_report",
)


# ---- helpers --------------------------------------------------------------


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data)}]}


def _err(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps({"error": message})}],
        "is_error": True,
    }


def _render_final_md(payload: dict[str, Any]) -> str:
    """Render the final report as analyst-friendly markdown.

    Inline markdown links pass through verbatim in prose fields; the
    structured `assumptions_and_sources` list is rendered as a clickable
    bullet list with links to both the source artifact and the primary URLs.
    """
    lines = [
        f"# {payload['asset']} — Lean Agentic Alpha Report",
        "",
        f"**Horizon:** {payload['horizon']}  ",
        f"**Cycle:** `{payload['cycle_id']}`  ",
        f"**Directive:** {payload['directive_version']}  ",
        "",
        "## Executive Summary",
        "",
        payload["executive_summary"],
        "",
        "## Scenarios",
        "",
        "| Scenario | Prob (%) | Implied Price | Return (%) | Key Assumption |",
        "|---|---:|---:|---:|---|",
    ]
    for sc in payload["scenarios"]:
        lines.append(
            f"| {sc.get('scenario','?')} | {sc.get('probability_pct','?')} | "
            f"{sc.get('implied_price','?')} | {sc.get('return_pct','?')} | "
            f"{sc.get('key_assumption','?')} |"
        )
    lines += [
        "",
        "## Portfolio Note",
        "",
        payload["portfolio_note"],
        "",
        "## Assumptions & Sources",
        "",
    ]
    for item in payload["assumptions_and_sources"]:
        claim = item.get("claim", "?")
        artifact = item.get("artifact_path", "")
        urls = item.get("source_urls", []) or []
        artifact_link = f"[{_artifact_label(artifact)}](./{artifact})" if artifact else ""
        url_links = ", ".join(
            f"[source {i+1}]({u})" for i, u in enumerate(urls) if u
        )
        suffix_parts: list[str] = []
        if artifact_link:
            suffix_parts.append(f"see {artifact_link}")
        if url_links:
            suffix_parts.append(url_links)
        suffix = f" ({'; '.join(suffix_parts)})" if suffix_parts else ""
        lines.append(f"- **{claim}**{suffix}")
    lines += [
        "",
        "## A7 Log",
        "",
        payload["a7_log_snippet"],
        "",
    ]
    return "\n".join(lines)


def _artifact_label(path: str) -> str:
    """artifacts/A2_1.md → A2.1; artifacts/A2_3_v2.md → A2.3 v2."""
    name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]  # A2_1 or A2_3_v2
    parts = name.split("_")
    if len(parts) >= 2 and parts[1].isdigit():
        label = f"{parts[0]}.{parts[1]}"
        if len(parts) >= 3 and parts[2].startswith("v"):
            label += f" {parts[2]}"
        return label
    return name

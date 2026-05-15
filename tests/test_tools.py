"""Layer 1: tools.py invariants — auto-approve gates, path sandboxing,
artifact discovery, finalize_report shape.

We invoke the tool handlers directly via the SdkMcpTool objects rather than
going through the MCP transport, so these run in milliseconds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lean_alpha import config, cycle as cy
from lean_alpha import tools


@pytest.fixture(autouse=True)
def isolated_runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(cy, "RUNS_DIR", tmp_path)
    return tmp_path


def _build(state):
    """Return a dict {tool_name: handler_callable} from the closure."""
    server = tools.build_mcp_server(state)
    # The server's `instance` exposes the registered tools via .request_handlers
    # in the MCP low-level Server, but the simplest approach is to reconstruct
    # the tool list ourselves: call build_mcp_server's internal closure.
    # Instead, we extract handlers by introspecting the server.instance.
    # The MCP Server stores tools via `_tool_handlers`. To avoid coupling to
    # MCP internals, we re-import the closure-bound tools by calling
    # build_mcp_server again and grabbing them directly from the SdkMcpTool
    # list passed to create_sdk_mcp_server. We modify build_mcp_server to
    # expose the tool list — see tools._build_handlers helper.
    # For now: rebuild via the public test helper below.
    return tools._build_handlers_for_test(state)


def _decode(result):
    """Tool results are {content: [{type:text, text: <json>}]}; return the dict."""
    text = result["content"][0]["text"]
    return json.loads(text)


@pytest.mark.asyncio
async def test_request_analyst_feedback_auto_approves(tmp_path):
    state = cy.CycleState(asset="A", horizon="1m")
    state.save()

    handlers = _build(state)
    out = await handlers["request_analyst_feedback"](
        {
            "stage": "A2.3",
            "artifact_path": "artifacts/A2_3.md",
            "a1_summary": "ok",
            "question": "",
        }
    )
    payload = _decode(out)
    assert payload["decision"] == "approve"
    reloaded = cy.CycleState.load(state.cycle_id)
    assert reloaded.gates["A2.3"].status == "approved"


@pytest.mark.asyncio
async def test_request_analyst_feedback_blocks_until_resolved(tmp_path):
    """In manual mode the tool must AWAIT the gate registry future and apply
    the analyst's decision (incl. iterate w/ feedback)."""
    import asyncio

    from lean_alpha import gates as g

    state = cy.CycleState(asset="A", horizon="1m")
    state.auto_approve_gates = False
    state.save()
    handlers = _build(state)

    # Resolve from the side after a brief delay
    async def _resolve_later():
        await asyncio.sleep(0.05)
        g.resolve_gate(
            state.cycle_id,
            "A2.3",
            g.GateResponse(decision="iterate", feedback="add FAA Stage-3 data"),
        )

    resolver = asyncio.create_task(_resolve_later())
    out = await handlers["request_analyst_feedback"](
        {"stage": "A2.3", "artifact_path": "artifacts/A2_3.md", "a1_summary": "ok", "question": ""}
    )
    await resolver

    payload = _decode(out)
    assert payload["decision"] == "iterate"
    assert payload["feedback"] == "add FAA Stage-3 data"

    reloaded = cy.CycleState.load(state.cycle_id)
    assert reloaded.gates["A2.3"].status == "iterate"
    assert reloaded.gates["A2.3"].feedback == "add FAA Stage-3 data"
    g.clear_for_cycle(state.cycle_id)


@pytest.mark.asyncio
async def test_request_analyst_feedback_rejects_substage(tmp_path):
    """A1 must not open gates at sub-stage names like A5.1 or A4.3."""
    state = cy.CycleState(asset="A", horizon="1m")
    state.save()
    handlers = _build(state)

    for bad in ("A5.1", "A5.2", "A4.3", "A2.1", "A99"):
        out = await handlers["request_analyst_feedback"](
            {"stage": bad, "artifact_path": "x", "a1_summary": "y", "question": ""}
        )
        assert out.get("is_error") is True, f"should reject gate '{bad}'"
        text = out["content"][0]["text"]
        assert "invalid gate name" in text


@pytest.mark.asyncio
async def test_request_analyst_feedback_accepts_canonical_gates(tmp_path):
    state = cy.CycleState(asset="A", horizon="1m")
    state.save()
    handlers = _build(state)
    for good in ("A2.3", "A5", "A7"):
        out = await handlers["request_analyst_feedback"](
            {"stage": good, "artifact_path": "x", "a1_summary": "y", "question": ""}
        )
        payload = _decode(out)
        assert payload["decision"] == "approve", f"gate {good} should auto-approve"


@pytest.mark.asyncio
async def test_read_artifact_sandbox(tmp_path):
    state = cy.CycleState(asset="A", horizon="1m")
    state.save()
    (state.artifact_dir() / "A2_1.md").write_text("hello")

    handlers = _build(state)
    ok = _decode(
        await handlers["read_artifact"]({"path": "artifacts/A2_1.md"})
    )
    assert ok["text"] == "hello"

    # Path traversal blocked
    bad = await handlers["read_artifact"]({"path": "../../../etc/passwd"})
    assert bad.get("is_error") is True


@pytest.mark.asyncio
async def test_list_artifacts(tmp_path):
    state = cy.CycleState(asset="A", horizon="1m")
    state.save()
    (state.artifact_dir() / "A2_1.md").write_text("x")
    (state.artifact_dir() / "A2_1.json").write_text("{}")

    handlers = _build(state)
    out = _decode(await handlers["list_artifacts"]({}))
    paths = sorted(item["path"] for item in out["artifacts"])
    assert paths == ["artifacts/A2_1.json", "artifacts/A2_1.md"]


@pytest.mark.asyncio
async def test_finalize_report_writes_files_and_renders_links(tmp_path):
    state = cy.CycleState(asset="ACHR", horizon="12m")
    state.save()
    handlers = _build(state)

    payload = {
        "executive_summary": (
            "ACHR expected return [+12%](./artifacts/A5_1.md), driven by "
            "[FAA cert progress](./artifacts/A2_3.md)."
        ),
        "scenarios": [
            {
                "scenario": "bull",
                "probability_pct": 30,
                "implied_price": 16,
                "return_pct": 50,
                "key_assumption": "[FAA cert by Q3](./artifacts/A3_3.md).",
            },
            {
                "scenario": "base",
                "probability_pct": 50,
                "implied_price": 13,
                "return_pct": 12,
                "key_assumption": "[Type cert mid-2027](./artifacts/A3_3.md).",
            },
            {
                "scenario": "bear",
                "probability_pct": 20,
                "implied_price": 6,
                "return_pct": -45,
                "key_assumption": "[Cert delays + dilution](./artifacts/A2_3.md).",
            },
        ],
        "portfolio_note": (
            "Weight [1.5%](./artifacts/A6_1.md), Sharpe impact "
            "[-0.05](./artifacts/A6_2.md)."
        ),
        "assumptions_and_sources": [
            {
                "claim": "FAA Stage 4 timeline",
                "artifact_path": "artifacts/A3_3.md",
                "source_urls": ["https://faa.gov/example", "https://archer.com/example"],
            },
            {
                "claim": "Stellantis manufacturing capacity",
                "artifact_path": "artifacts/A4_1.md",
                "source_urls": [],
            },
        ],
        "a7_log_snippet": "[verified=92%, signed=true, duration=18min](./artifacts/A7.md)",
    }
    out = _decode(await handlers["finalize_report"](payload))
    assert out["completed"] is True
    assert state.final_report_path_md().exists()

    reloaded = cy.CycleState.load(state.cycle_id)
    assert reloaded.completed

    rendered = state.final_report_path_md().read_text()
    assert "ACHR — Lean Agentic Alpha Report" in rendered
    # Inline links from prose fields pass through verbatim
    assert "[+12%](./artifacts/A5_1.md)" in rendered
    assert "[FAA cert by Q3](./artifacts/A3_3.md)" in rendered
    # Structured sources rendered as clickable bullets
    assert "**FAA Stage 4 timeline**" in rendered
    assert "[A3.3](./artifacts/A3_3.md)" in rendered
    assert "[source 1](https://faa.gov/example)" in rendered
    # Entry without source_urls still renders the artifact link
    assert "**Stellantis manufacturing capacity**" in rendered
    assert "[A4.1](./artifacts/A4_1.md)" in rendered


@pytest.mark.asyncio
async def test_finalize_report_rejects_unstructured_assumptions(tmp_path):
    """Old-style list[str] for assumptions_and_sources must be rejected."""
    state = cy.CycleState(asset="A", horizon="1m")
    state.save()
    handlers = _build(state)
    out = await handlers["finalize_report"](
        {
            "executive_summary": "x",
            "scenarios": [
                {"scenario": "bull", "probability_pct": 30, "implied_price": 1,
                 "return_pct": 1, "key_assumption": "[a](./artifacts/A2_1.md)"},
                {"scenario": "base", "probability_pct": 50, "implied_price": 1,
                 "return_pct": 1, "key_assumption": "[b](./artifacts/A2_1.md)"},
                {"scenario": "bear", "probability_pct": 20, "implied_price": 1,
                 "return_pct": 1, "key_assumption": "[c](./artifacts/A2_1.md)"},
            ],
            "portfolio_note": "x",
            "assumptions_and_sources": ["plain string item"],  # WRONG SHAPE
            "a7_log_snippet": "x",
        }
    )
    assert out.get("is_error") is True
    assert "{claim, artifact_path, source_urls}" in out["content"][0]["text"]


@pytest.mark.asyncio
async def test_finalize_report_rejects_missing_source_fields(tmp_path):
    state = cy.CycleState(asset="A", horizon="1m")
    state.save()
    handlers = _build(state)
    out = await handlers["finalize_report"](
        {
            "executive_summary": "x",
            "scenarios": [
                {"scenario": "bull", "probability_pct": 30, "implied_price": 1,
                 "return_pct": 1, "key_assumption": "x"},
                {"scenario": "base", "probability_pct": 50, "implied_price": 1,
                 "return_pct": 1, "key_assumption": "x"},
                {"scenario": "bear", "probability_pct": 20, "implied_price": 1,
                 "return_pct": 1, "key_assumption": "x"},
            ],
            "portfolio_note": "x",
            "assumptions_and_sources": [
                {"claim": "missing source_urls", "artifact_path": "artifacts/A2_1.md"},
            ],
            "a7_log_snippet": "x",
        }
    )
    assert out.get("is_error") is True
    assert "source_urls" in out["content"][0]["text"]


@pytest.mark.asyncio
async def test_finalize_report_rejects_wrong_scenario_count(tmp_path):
    state = cy.CycleState(asset="A", horizon="1m")
    state.save()
    handlers = _build(state)
    out = await handlers["finalize_report"](
        {
            "executive_summary": "x",
            "scenarios": [{"scenario": "base"}],  # wrong count
            "portfolio_note": "x",
            "assumptions_and_sources": [],
            "a7_log_snippet": "x",
        }
    )
    assert out.get("is_error") is True


@pytest.mark.asyncio
async def test_dispatch_specialist_unknown_name(tmp_path):
    state = cy.CycleState(asset="A", horizon="1m")
    state.save()
    handlers = _build(state)
    out = await handlers["dispatch_specialist"](
        {"name": "ZZ.9", "brief": "x", "prior_artifact_paths": [], "feedback": ""}
    )
    assert out.get("is_error") is True


@pytest.mark.asyncio
async def test_dispatch_specialist_missing_prior(tmp_path):
    state = cy.CycleState(asset="A", horizon="1m")
    state.save()
    handlers = _build(state)
    out = await handlers["dispatch_specialist"](
        {
            "name": "A2.2",
            "brief": "x",
            "prior_artifact_paths": ["artifacts/A2_1.md"],  # not yet created
            "feedback": "",
        }
    )
    assert out.get("is_error") is True

"""Layer 1: gate registry — open / resolve / cancel."""

from __future__ import annotations

import asyncio

import pytest

from lean_alpha.gates import (
    GateRequest,
    GateResponse,
    clear_for_cycle,
    list_open_requests,
    open_gate,
    resolve_gate,
)


def _req(cycle="c1", stage="A2.3"):
    return GateRequest(
        cycle_id=cycle,
        stage=stage,
        artifact_path="artifacts/A2_3.md",
        a1_summary="ok",
    )


@pytest.mark.asyncio
async def test_open_then_resolve():
    fut = open_gate(_req())
    # Resolve from another task simulating the CLI / HTTP endpoint
    asyncio.get_running_loop().call_later(
        0.01,
        lambda: resolve_gate("c1", "A2.3", GateResponse(decision="approve", feedback="ok")),
    )
    response = await asyncio.wait_for(fut, timeout=2)
    assert response.decision == "approve"
    assert response.feedback == "ok"
    clear_for_cycle("c1")


@pytest.mark.asyncio
async def test_resolve_returns_false_when_no_pending():
    """Calling resolve_gate without an open future should return False."""
    assert resolve_gate("ghost", "A2.3", GateResponse(decision="approve")) is False


@pytest.mark.asyncio
async def test_open_gate_returns_same_future_for_same_key():
    f1 = open_gate(_req())
    f2 = open_gate(_req())
    assert f1 is f2
    clear_for_cycle("c1")


@pytest.mark.asyncio
async def test_list_open_requests_filters_by_cycle():
    open_gate(_req("c-A", "A2.3"))
    open_gate(_req("c-B", "A5"))

    cA = list_open_requests("c-A")
    assert len(cA) == 1 and cA[0].cycle_id == "c-A"
    all_ = list_open_requests()
    assert len(all_) >= 2

    clear_for_cycle("c-A")
    clear_for_cycle("c-B")


@pytest.mark.asyncio
async def test_cancel_for_cycle_cancels_pending():
    fut = open_gate(_req("c-cancel", "A2.3"))
    clear_for_cycle("c-cancel")
    with pytest.raises(asyncio.CancelledError):
        await fut


@pytest.mark.asyncio
async def test_iterate_with_feedback():
    fut = open_gate(_req("c2", "A5"))
    asyncio.get_running_loop().call_later(
        0.01,
        lambda: resolve_gate(
            "c2",
            "A5",
            GateResponse(
                decision="iterate",
                feedback="redo with FAA Stage-3 data",
                stages_invalidated=("A3.1",),
            ),
        ),
    )
    response = await asyncio.wait_for(fut, timeout=2)
    assert response.decision == "iterate"
    assert response.feedback == "redo with FAA Stage-3 data"
    assert response.stages_invalidated == ("A3.1",)
    clear_for_cycle("c2")

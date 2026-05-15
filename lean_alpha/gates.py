"""Gate resolution registry — async futures keyed by (cycle_id, gate_name).

When ``request_analyst_feedback`` runs in non-auto-approve mode, the tool
handler awaits a future from this registry. An external caller (CLI prompt
in M3, HTTP endpoint in M5) resolves it with the analyst's decision.

Single-process for now — fine for the local CLI and a single-worker FastAPI.
Replace with a Redis-backed pub/sub or DB-backed queue if you ever need
multi-worker.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal


GateDecision = Literal["approve", "iterate", "decline"]


@dataclass
class GateRequest:
    cycle_id: str
    stage: str            # one of "A2.3" | "A5" | "A7"
    artifact_path: str
    a1_summary: str
    question: str = ""


@dataclass
class GateResponse:
    decision: GateDecision
    feedback: str = ""
    stages_invalidated: tuple[str, ...] = ()


_pending: dict[tuple[str, str], asyncio.Future[GateResponse]] = {}
_open_requests: dict[tuple[str, str], GateRequest] = {}


def open_gate(req: GateRequest) -> asyncio.Future[GateResponse]:
    """Called by the tool handler. Creates (or returns existing) future for
    this (cycle_id, stage). The handler awaits this future."""
    key = (req.cycle_id, req.stage)
    if key in _pending and not _pending[key].done():
        return _pending[key]
    fut: asyncio.Future[GateResponse] = asyncio.get_running_loop().create_future()
    _pending[key] = fut
    _open_requests[key] = req
    return fut


def resolve_gate(
    cycle_id: str, stage: str, response: GateResponse
) -> bool:
    """Called by the CLI / HTTP endpoint to deliver the analyst's decision.
    Returns True if a pending future was resolved, False if no gate was open."""
    key = (cycle_id, stage)
    fut = _pending.get(key)
    if fut is None or fut.done():
        return False
    fut.set_result(response)
    _open_requests.pop(key, None)
    return True


def list_open_requests(cycle_id: str | None = None) -> list[GateRequest]:
    """Snapshot of currently-blocking gate requests."""
    items = list(_open_requests.values())
    if cycle_id is not None:
        items = [r for r in items if r.cycle_id == cycle_id]
    return items


def clear_for_cycle(cycle_id: str) -> None:
    """Drop any pending requests for this cycle (use on cycle end / abort)."""
    keys = [k for k in _pending if k[0] == cycle_id]
    for k in keys:
        fut = _pending.pop(k, None)
        if fut and not fut.done():
            fut.cancel()
        _open_requests.pop(k, None)

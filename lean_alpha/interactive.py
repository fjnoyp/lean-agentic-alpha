"""CLI gate watcher — pairs with non-auto-approve cycles.

Runs concurrently with the coordinator. Watches the gate registry; when A1
opens a gate, prints the artifact + summary to the terminal, asks the analyst
for a decision, and resolves the future.

For M3 we read the analyst's response from stdin. M5 swaps this for an HTTP
endpoint so a UI can drive it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from .config import RUNS_DIR
from .gates import GateResponse, list_open_requests, resolve_gate


async def gate_watcher(cycle_id: str, *, poll_interval: float = 0.5) -> None:
    """Continuously watch for open gate requests on this cycle and prompt
    the analyst at the terminal. Exits when the cycle's task is done — the
    coordinator cancels this task when it finishes."""
    seen: set[tuple[str, str]] = set()
    while True:
        for req in list_open_requests(cycle_id):
            key = (req.cycle_id, req.stage)
            if key in seen:
                continue
            seen.add(key)
            response = await asyncio.to_thread(_prompt_analyst, req)
            resolve_gate(req.cycle_id, req.stage, response)
        await asyncio.sleep(poll_interval)


def _prompt_analyst(req) -> GateResponse:
    """Blocking stdin prompt — runs in a thread so it doesn't block the loop."""
    artifact_path = RUNS_DIR / req.cycle_id / req.artifact_path
    print()
    print("=" * 70)
    print(f"  ANALYST GATE — stage {req.stage}")
    print("=" * 70)
    print()
    print(f"  Artifact: {artifact_path}")
    print()
    print(f"  A1 summary:")
    for line in (req.a1_summary or "").splitlines() or [""]:
        print(f"    {line}")
    if req.question:
        print()
        print(f"  A1 asks: {req.question}")
    print()
    print(f"  Open the artifact in another window to review.")
    print(f"  Decision options: [a]pprove / [i]terate / [d]ecline")
    print()

    while True:
        choice = input("  > ").strip().lower()
        if choice in ("a", "approve"):
            decision = "approve"
            break
        if choice in ("i", "iterate"):
            decision = "iterate"
            break
        if choice in ("d", "decline"):
            decision = "decline"
            break
        print("  enter 'a', 'i', or 'd'.")

    feedback = input("  Optional feedback (free text, blank ok): ").strip()
    print()
    return GateResponse(decision=decision, feedback=feedback)


async def run_with_watcher(coro_factory, cycle_id: str):
    """Run a coordinator coroutine alongside the gate watcher.

    `coro_factory` is a zero-arg callable returning the coordinator coroutine.
    The watcher is cancelled when the coordinator finishes.
    """
    coordinator_task = asyncio.create_task(coro_factory())
    watcher_task = asyncio.create_task(gate_watcher(cycle_id))
    try:
        return await coordinator_task
    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass

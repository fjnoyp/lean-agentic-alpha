"""Per-cycle state, on-disk layout, gate tracking.

A cycle directory:

    runs/cycle_<id>/
      cycle.json              ← state (gates, metadata, completion)
      conversation.jsonl      ← A1's append-only message log (M3+)
      artifacts/              ← specialist outputs (.md + .json)
        A2_1.md, A2_1.json, ...
      final_report.md         ← A1's synthesis
      final_report.json
      audit/                  ← (optional) per-cycle audit data

Gates: in M2 every gate auto-approves the moment A1 calls
``request_analyst_feedback`` — we are wiring the data layer; the human-in-loop
behaviour lands in M3. The same fields will carry the real decision.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal

from .config import DIRECTIVE_VERSION, PROMPT_PACK_VERSION, RUNS_DIR


GateStatus = Literal["pending", "approved", "declined", "iterate"]


@dataclass
class GateState:
    status: GateStatus = "pending"
    feedback: str = ""
    ts: float | None = None  # when the decision was recorded


@dataclass
class CycleState:
    asset: str
    horizon: str
    cycle_id: str = field(default_factory=lambda: f"cycle_{uuid.uuid4().hex[:10]}")
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    completed: bool = False

    directive_version: str = DIRECTIVE_VERSION
    prompt_pack_version: str = PROMPT_PACK_VERSION

    # Gates the runbook explicitly calls out. Add more as governance evolves.
    gates: dict[str, GateState] = field(
        default_factory=lambda: {
            "A2.3": GateState(),
            "A5":   GateState(),
            "A7":   GateState(),
        }
    )

    # Per-stage iteration counter so we can cap at iteration_max.
    iteration_count: dict[str, int] = field(default_factory=dict)

    # In M2 this is constant True (auto-approve). In M3 it becomes a real toggle
    # so the analyst (or test harness) can opt out of blocking gates.
    auto_approve_gates: bool = True

    def dir(self) -> Path:
        d = RUNS_DIR / self.cycle_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def artifact_dir(self) -> Path:
        d = self.dir() / "artifacts"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def state_path(self) -> Path:
        return self.dir() / "cycle.json"

    def conversation_path(self) -> Path:
        return self.dir() / "conversation.jsonl"

    def final_report_path_md(self) -> Path:
        return self.dir() / "final_report.md"

    def final_report_path_json(self) -> Path:
        return self.dir() / "final_report.json"

    def save(self) -> None:
        # GateState dataclasses serialize via asdict
        payload = asdict(self)
        self.state_path().write_text(json.dumps(payload, indent=2, default=str))

    @classmethod
    def load(cls, cycle_id: str) -> "CycleState":
        path = RUNS_DIR / cycle_id / "cycle.json"
        if not path.exists():
            raise FileNotFoundError(f"No cycle at {path}")
        data = json.loads(path.read_text())
        # Re-hydrate gate states
        gates = {k: GateState(**v) for k, v in (data.pop("gates") or {}).items()}
        return cls(**data, gates=gates)

    def record_gate(self, gate: str, status: GateStatus, feedback: str = "") -> None:
        if gate not in self.gates:
            self.gates[gate] = GateState()
        self.gates[gate] = GateState(status=status, feedback=feedback, ts=time.time())
        self.save()

    def record_iteration(self, stage: str) -> int:
        self.iteration_count[stage] = self.iteration_count.get(stage, 0) + 1
        self.save()
        return self.iteration_count[stage]

    def mark_complete(self) -> None:
        self.completed = True
        self.completed_at = time.time()
        self.save()


def list_cycles() -> list[CycleState]:
    """Cycles found on disk, newest first."""
    if not RUNS_DIR.exists():
        return []
    out: list[CycleState] = []
    for d in sorted(RUNS_DIR.glob("cycle_*"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append(CycleState.load(d.name))
        except Exception:  # noqa: BLE001
            continue
    return out


def relative_artifact_path(cycle: CycleState, abs_path: Path) -> str:
    """Render an artifact's path as a relative string for use in briefs."""
    return str(Path(abs_path).relative_to(cycle.dir()))

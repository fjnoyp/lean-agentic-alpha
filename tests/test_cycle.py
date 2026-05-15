"""Layer 1: cycle state persistence + gate tracking."""

from __future__ import annotations

from pathlib import Path

import pytest

from lean_alpha import cycle as cy
from lean_alpha import config


@pytest.fixture(autouse=True)
def isolated_runs_dir(tmp_path, monkeypatch):
    """Every test gets a fresh runs/ directory."""
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(cy, "RUNS_DIR", tmp_path)
    return tmp_path


def test_create_and_save(isolated_runs_dir):
    state = cy.CycleState(asset="ACHR", horizon="12m")
    state.save()
    assert state.state_path().exists()
    assert state.artifact_dir().exists()
    assert state.cycle_id.startswith("cycle_")


def test_load_roundtrip(isolated_runs_dir):
    state = cy.CycleState(asset="ACHR", horizon="12m")
    state.save()

    loaded = cy.CycleState.load(state.cycle_id)
    assert loaded.asset == "ACHR"
    assert loaded.horizon == "12m"
    assert loaded.gates["A2.3"].status == "pending"


def test_record_gate(isolated_runs_dir):
    state = cy.CycleState(asset="ACHR", horizon="12m")
    state.record_gate("A2.3", "approved", "verified by analyst")
    loaded = cy.CycleState.load(state.cycle_id)
    assert loaded.gates["A2.3"].status == "approved"
    assert loaded.gates["A2.3"].feedback == "verified by analyst"
    assert loaded.gates["A2.3"].ts is not None


def test_record_iteration(isolated_runs_dir):
    state = cy.CycleState(asset="ACHR", horizon="12m")
    assert state.record_iteration("A2.3") == 1
    assert state.record_iteration("A2.3") == 2
    assert state.record_iteration("A4.1") == 1
    loaded = cy.CycleState.load(state.cycle_id)
    assert loaded.iteration_count == {"A2.3": 2, "A4.1": 1}


def test_mark_complete(isolated_runs_dir):
    state = cy.CycleState(asset="ACHR", horizon="12m")
    state.mark_complete()
    loaded = cy.CycleState.load(state.cycle_id)
    assert loaded.completed
    assert loaded.completed_at is not None


def test_list_cycles_empty(isolated_runs_dir):
    assert cy.list_cycles() == []


def test_list_cycles_returns_in_order(isolated_runs_dir):
    import time
    s1 = cy.CycleState(asset="A", horizon="1m")
    s1.save()
    time.sleep(0.01)
    s2 = cy.CycleState(asset="B", horizon="1m")
    s2.save()
    cycles = cy.list_cycles()
    assert len(cycles) == 2
    # Newest first
    assert cycles[0].cycle_id == s2.cycle_id


def test_relative_artifact_path(isolated_runs_dir):
    state = cy.CycleState(asset="A", horizon="1m")
    state.save()
    artifact = state.artifact_dir() / "A2_1.md"
    artifact.write_text("ok")
    rel = cy.relative_artifact_path(state, artifact)
    assert rel == "artifacts/A2_1.md"

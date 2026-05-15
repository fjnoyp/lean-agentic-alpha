"""Layer 1: pure-Python tests with no API calls.

Verifies prompt loading, token substitution, and registry consistency.
Runs in milliseconds.
"""

from __future__ import annotations

import pytest

from lean_alpha import specialists
from lean_alpha.config import PROMPTS_DIR


def test_directive_loads():
    text = (PROMPTS_DIR / "_directive.md").read_text()
    assert "Deep Research Directive" in text
    assert "primary public sources" in text


def test_artifact_protocol_has_no_path_prefix():
    """The protocol must NOT instruct the model to prefix `artifacts/` —
    the dispatcher already sets cwd to the artifact dir. Path-prefix
    instructions cause nested artifacts/artifacts/ folders."""
    text = (PROMPTS_DIR / "_artifact_protocol.md").read_text()
    assert "do **NOT** prefix" in text or "do NOT prefix" in text


def test_specialist_prompts_substitute_tokens():
    for name, cfg in specialists.REGISTRY.items():
        rendered = specialists._load_prompt(cfg.prompt_file)
        assert "{{DIRECTIVE}}" not in rendered, f"{name} has unsubstituted DIRECTIVE token"
        assert (
            "{{ARTIFACT_PROTOCOL}}" not in rendered
        ), f"{name} has unsubstituted ARTIFACT_PROTOCOL token"
        # Spot-check substitution actually happened
        assert "Deep Research Directive" in rendered or "Lean Agentic Alpha" in rendered


def test_stage_filename_replacement():
    assert specialists._stage_filename("A2.1") == "A2_1"
    assert specialists._stage_filename("A4.3") == "A4_3"
    assert specialists._stage_filename("A7") == "A7"


def test_registry_has_at_least_a21():
    """M1 only registers A2.1; future milestones add the rest."""
    assert "A2.1" in specialists.REGISTRY

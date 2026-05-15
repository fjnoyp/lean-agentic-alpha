"""Layer 3: real-API integration test using Haiku.

Slow + costs cents. Skipped by default; opt in with:

    LEAN_ALPHA_SLOW_TESTS=1 pytest tests/test_dispatch_haiku.py

Validates that a real specialist run end-to-end: spawns an agent, web-search
returns, the model writes valid artifacts, the JSON passes schema validation.
We use Haiku because it's fast/cheap; output quality won't match Sonnet but
the dispatch shell is exactly the same code path.
"""

from __future__ import annotations

import os

import pytest

from lean_alpha import config
from lean_alpha.specialists import run_specialist


SLOW = os.environ.get("LEAN_ALPHA_SLOW_TESTS") == "1"


@pytest.mark.skipif(not SLOW, reason="set LEAN_ALPHA_SLOW_TESTS=1 to run")
@pytest.mark.asyncio
async def test_a21_runs_with_haiku(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODEL", "claude-haiku-4-5")
    # Use a very small, well-known asset to keep Haiku within its competence.
    result = await run_specialist(
        "A2.1",
        brief="Apple Inc, 12 months",
        artifact_dir=tmp_path,
    )
    assert result.artifact_md.exists()
    assert result.artifact_json.exists()
    assert result.summary
    assert result.input_tokens > 0
    assert result.output_tokens > 0

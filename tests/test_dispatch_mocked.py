"""Layer 1: dispatch-shell tests with the SDK mocked.

Verifies that `run_specialist`:
- Sets cwd to the artifact directory.
- Wires up the right tools (with WebSearch when needs_web=True).
- Substitutes the system prompt.
- Picks up the produced artifacts and validates them.
- Surfaces token usage from the ResultMessage.

Uses a fake `query` that the test injects via monkeypatch. The fake creates
the expected files on disk in `options.cwd`, then yields a fake assistant
message and a fake result message — same shape the real SDK would produce.

Runs in milliseconds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


@dataclass
class _FakeText:
    text: str
    type: str = "text"


@dataclass
class _FakeAssistant:
    content: list[Any]
    type: str = "assistant"


@dataclass
class _FakeResult:
    subtype: str = "success"
    total_cost_usd: float = 0.001
    usage: dict[str, int] | None = None
    type: str = "result"

    def __post_init__(self):
        if self.usage is None:
            self.usage = {"input_tokens": 100, "output_tokens": 50}


async def _fake_query_factory(payload: dict[str, Any]):
    """Returns an async generator function that the test can substitute for
    `claude_agent_sdk.query`. The fake mimics the SDK by writing the artifacts
    into `options.cwd` before yielding messages."""

    async def _fake_query(*, prompt, options, transport=None):
        cwd = Path(options.cwd)
        # Mimic what a well-behaved specialist would do.
        (cwd / "A2_1.md").write_text("# Stub markdown\n")
        (cwd / "A2_1.json").write_text(json.dumps(payload))

        yield _FakeAssistant(content=[_FakeText(text="Wrote both artifacts.")])
        yield _FakeResult()

    return _fake_query


# --- The SDK's TextBlock / AssistantMessage / ResultMessage are imported by the
# specialists module directly, and the dispatcher uses isinstance() checks. We
# patch the names in `lean_alpha.specialists` so isinstance works on our fakes
# (because Python isinstance uses the symbol bound at the call site's module).
# A simpler approach: patch the imported symbols in the specialists module so
# that our fakes pass isinstance.


@pytest.fixture
def patched_sdk(monkeypatch):
    """Swap SDK symbols in lean_alpha.specialists for our fake classes."""
    from lean_alpha import specialists as sp

    monkeypatch.setattr(sp, "TextBlock", _FakeText)
    monkeypatch.setattr(sp, "AssistantMessage", _FakeAssistant)
    monkeypatch.setattr(sp, "ResultMessage", _FakeResult)
    return sp


@pytest.mark.asyncio
async def test_dispatch_writes_artifacts_and_returns_result(patched_sdk, tmp_path, monkeypatch):
    payload = {
        "stage": "A2.1",
        "summary": "Test summary.",
        "rows": [
            {
                "label": "Revenue growth",
                "value": "+15%",
                "source_url": "https://example.com",
                "confidence": "high",
                "assumed": False,
            },
        ],
    }
    fake_query = await _fake_query_factory(payload)
    monkeypatch.setattr(patched_sdk, "query", fake_query)

    result = await patched_sdk.run_specialist(
        "A2.1", brief="test brief", artifact_dir=tmp_path
    )
    assert result.artifact_md == tmp_path / "A2_1.md"
    assert result.artifact_json == tmp_path / "A2_1.json"
    assert result.summary == "Test summary."
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.cost_usd == 0.001


@pytest.mark.asyncio
async def test_dispatch_wires_up_options(patched_sdk, tmp_path, monkeypatch):
    """Capture the options the dispatcher passes to the SDK and assert
    on cwd, tools, system_prompt presence, and adaptive thinking."""
    captured: dict[str, Any] = {}

    payload = {
        "stage": "A2.1",
        "summary": "x",
        "rows": [
            {
                "label": "a",
                "value": "b",
                "source_url": "https://c",
                "confidence": "high",
                "assumed": False,
            }
        ],
    }

    async def capturing_query(*, prompt, options, transport=None):
        captured["prompt"] = prompt
        captured["options"] = options
        cwd = Path(options.cwd)
        (cwd / "A2_1.md").write_text("ok")
        (cwd / "A2_1.json").write_text(json.dumps(payload))
        yield _FakeAssistant(content=[_FakeText(text="done")])
        yield _FakeResult()

    monkeypatch.setattr(patched_sdk, "query", capturing_query)

    await patched_sdk.run_specialist("A2.1", brief="brief", artifact_dir=tmp_path)

    opts = captured["options"]
    assert str(opts.cwd) == str(tmp_path)
    assert "Read" in opts.tools and "Write" in opts.tools
    assert "WebSearch" in opts.tools  # A2.1 has needs_web=True
    assert isinstance(opts.system_prompt, str) and "Lean Agentic Alpha" in opts.system_prompt
    assert opts.thinking == {"type": "adaptive"}
    assert opts.permission_mode == "bypassPermissions"
    assert opts.setting_sources == []
    assert "test brief" in captured["prompt"] or "brief" in captured["prompt"]


@pytest.mark.asyncio
async def test_dispatch_raises_when_artifacts_missing(patched_sdk, tmp_path, monkeypatch):
    """If the specialist doesn't produce both files, the dispatcher must fail."""

    async def bad_query(*, prompt, options, transport=None):
        # Don't write anything.
        yield _FakeAssistant(content=[_FakeText(text="forgot to write")])
        yield _FakeResult()

    monkeypatch.setattr(patched_sdk, "query", bad_query)

    with pytest.raises(RuntimeError, match="did not produce required artifacts"):
        await patched_sdk.run_specialist("A2.1", brief="x", artifact_dir=tmp_path)


@pytest.mark.asyncio
async def test_dispatch_validates_json_schema(patched_sdk, tmp_path, monkeypatch):
    """If the JSON is malformed, the dispatcher must fail with a schema error."""

    async def malformed(*, prompt, options, transport=None):
        cwd = Path(options.cwd)
        (cwd / "A2_1.md").write_text("ok")
        (cwd / "A2_1.json").write_text(
            json.dumps(
                {
                    "stage": "A2.1",
                    "summary": "x",
                    "rows": [{"label": "a", "value": "b"}],  # missing fields
                }
            )
        )
        yield _FakeAssistant(content=[_FakeText(text="done")])
        yield _FakeResult()

    monkeypatch.setattr(patched_sdk, "query", malformed)

    with pytest.raises(RuntimeError, match="missing field"):
        await patched_sdk.run_specialist("A2.1", brief="x", artifact_dir=tmp_path)


@pytest.mark.asyncio
async def test_dispatch_iteration_versions_filename(patched_sdk, tmp_path, monkeypatch):
    payload = {
        "stage": "A2.1",
        "summary": "v2",
        "rows": [
            {
                "label": "a",
                "value": "b",
                "source_url": "https://c",
                "confidence": "high",
                "assumed": False,
            }
        ],
    }

    async def fake(*, prompt, options, transport=None):
        cwd = Path(options.cwd)
        (cwd / "A2_1_v2.md").write_text("v2")
        (cwd / "A2_1_v2.json").write_text(json.dumps(payload))
        yield _FakeAssistant(content=[_FakeText(text="done")])
        yield _FakeResult()

    monkeypatch.setattr(patched_sdk, "query", fake)

    result = await patched_sdk.run_specialist(
        "A2.1", brief="x", artifact_dir=tmp_path, iteration=1, feedback="redo"
    )
    assert result.artifact_md.name == "A2_1_v2.md"
    assert result.artifact_json.name == "A2_1_v2.json"

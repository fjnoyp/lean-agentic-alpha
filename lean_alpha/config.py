"""Runtime config — model, paths, .env loading."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# Sonnet 4.6 by default — same dial we set in .env.
MODEL = os.environ.get("LEAN_ALPHA_MODEL", "claude-sonnet-4-6")
API_KEY = os.environ.get("ANTHROPIC_API_KEY")

DIRECTIVE_VERSION = "v1.0"
PROMPT_PACK_VERSION = "v2.0"

if not API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY not set. Add it to .env at the project root.")

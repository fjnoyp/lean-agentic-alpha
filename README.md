# Lean Agentic Alpha

A multi-agent research framework for buy-side investment teams. One analyst brief — "asset, time horizon" — is decomposed into a structured, citation-traceable research cycle that produces an analyst-grade report with three-scenario valuation, portfolio weighting, and a full audit log.

Framework authored by **Neil Brown** (Stanford Capstone, October 2025). The agent prompts in `lean_alpha/prompts/` embed the Deep Research Directive v1.0 and Prompt Pack v2.0.

## What it does, in finance terms

Replaces the broad, noisy data-trawl phase of equity research with a disciplined seven-stage pipeline. The analyst stays in the loop at three checkpoints and signs off the final report.

| Stage | Question it answers |
|---|---|
| **A1 Conductor** | Decompose the brief, run the chain, manage gates |
| **A2 Value Drivers** | What drove 5–10y TSR? Which structural levers persist? What are the 3 forward drivers for the horizon? *(gate)* |
| **A3 Base Rates** | Macro forecasts, sector growth, regulatory/timing catalysts |
| **A4 Winners/Losers** | Peer metrics → competitive edges → probability ranking |
| **A5 Focus Integrator** | Three-scenario valuation + stress test *(gate)* |
| **A6 Portfolio** | Recommended weight and portfolio impact |
| **A7 Evaluator** | Audit log: % verified cells, source count, sign-off *(gate)* |

Every numeric input is either cited or labelled `ASSUMED`. A1 cannot advance to portfolio construction without analyst verification at A2.3. Only public/synthetic data is permitted in this sandbox.

Pilot runs (Archer Aviation, DR Horton, CrowdStrike, Robotics ETF) produced analyst-grade reports in **5–30 minutes** versus a 3–4 hour analyst baseline, with full citation traceability. See `archer-report/` for a sample rendered output.

## Architecture

```
Analyst brief → A1 (Conductor) ─┬─► A2.1 → A2.2 → A2.3  ─[gate]─►
                                ├─► A3.1 ‖ A3.2 ‖ A3.3  (parallel)
                                ├─► A4.1 → A4.2 → A4.3
                                ├─► A5.1 → A5.2          ─[gate]─►
                                ├─► A6.1 → A6.2
                                └─► A7                   ─[gate]─► final_report.md
```

A1 holds the project context and orchestration logic; the fourteen specialists (A2.1 … A7) are **stateless, one-shot agents** that read upstream artifacts, perform their narrow task, and write a `<STAGE>.md` + `<STAGE>.json` pair into the cycle's artifact directory. The JSON envelope (`stage`, `summary`, `rows[]` with `confidence` and `source_url`) is validated at the boundary so prompt drift can't corrupt downstream stages.

Specialists run in parallel wherever the DAG allows (A3.1/A3.2/A3.3 fan out together). Per-stage budgets, iteration caps (max 5 re-runs with feedback), and gate enforcement live in the harness — A1 cannot bypass them.

## Why the Claude Agent SDK

The framework needs three things from its runtime, and the Claude Agent SDK provides each as a primitive rather than something we had to build:

1. **Per-agent tool sandboxing.** Each specialist runs as its own SDK agent loop with `cwd` pinned to the cycle's artifact directory and only `Read / Write / Glob / Grep` (plus optional `WebSearch / WebFetch`) allowed. Filesystem access is confined and Bash is unavailable — load-bearing for the "only public data" rule.
2. **Custom tools as a typed contract.** A1's five tools (`dispatch_specialist`, `request_analyst_feedback`, `read_artifact`, `list_artifacts`, `finalize_report`) are registered as an in-process MCP server. Hard gates (e.g. "no A6 dispatch before A2.3 is analyst-verified") are enforced inside those handlers — A1 *cannot* route around them, and the schema returns a structured error explaining the violation.
3. **Conversation transcripts and usage telemetry as first-class output.** Every cycle writes a JSONL of the A1 loop plus a per-stage cost/token audit, which is what makes the A7 "% verified cells / source count / duration" log possible without bespoke logging.

In short: the SDK lets us treat **governance as code** (sandboxing, tool allowlists, gate enforcement) rather than as a prompt instruction, which is what we need for an auditable analyst workflow.

## Repo layout

```
lean_alpha/
├── coordinator.py      A1 main loop (single async query() over the SDK)
├── specialists.py      Specialist registry + per-stage dispatcher
├── tools.py            A1's five custom tools (MCP server)
├── gates.py            Analyst gate request/response futures
├── cycle.py            CycleState — on-disk artifact directory model
├── prompts/            A1 + A2.1 … A7 system prompts (the IP)
├── render_site.py      Render a finished cycle to a static HTML report
└── cli.py              `lean-alpha run-cycle "<asset>" "<horizon>"`
archer-report/          Sample rendered output (Archer Aviation, 12mo)
tests/                  Pytest suite (asyncio_mode = auto)
```

## Quick start

```bash
pip install -e .
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# Run an end-to-end cycle (auto-approves gates):
lean-alpha run-cycle "Archer Aviation" "12 months"

# Or with an analyst-in-the-loop at the terminal:
lean-alpha run-cycle "Archer Aviation" "12 months" --interactive

# Render the finished cycle to a static site:
lean-alpha list-cycles
lean-alpha render-site cycle_xxxxxxxxxx
```

Outputs land in `runs/<cycle_id>/`: per-stage artifacts under `artifacts/`, the conversation log, an A1 usage audit, and the final `final_report.md` / `final_report.json`.

## Status

Research prototype, not production. The next phase moves from single-asset cycles to batched runs across a portfolio, with the analyst approving stages asynchronously rather than blocking inline.

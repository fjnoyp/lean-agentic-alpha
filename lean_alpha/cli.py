"""CLI for ad-hoc invocation.

Subcommands:
  run-specialist <name> "<brief>"     — run a single specialist (M1 test harness)
  run-cycle      "<asset>" "<horizon>" — run the full A1-coordinated cycle (M2)
  list-cycles                          — show cycles on disk
"""

from __future__ import annotations

import argparse
import asyncio
import time
import uuid
from pathlib import Path

from .config import RUNS_DIR
from .specialists import run_specialist_sync


def _new_test_artifact_dir() -> Path:
    cid = f"test_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    d = RUNS_DIR / cid / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cmd_run_specialist(args: argparse.Namespace) -> None:
    artifact_dir = args.cycle_dir or _new_test_artifact_dir()
    print(f"[dispatch] {args.name} → {artifact_dir}")
    result = run_specialist_sync(
        args.name,
        brief=args.brief,
        artifact_dir=artifact_dir,
        prior_artifact_paths=args.prior,
        feedback=args.feedback,
        iteration=args.iteration,
    )
    print()
    print(f"[done] {result.name} in {result.duration_s:.1f}s")
    print(f"  md:   {result.artifact_md}")
    print(f"  json: {result.artifact_json}")
    print(f"  tokens: {result.input_tokens} in / {result.output_tokens} out")
    if result.cost_usd is not None:
        print(f"  cost: ${result.cost_usd:.4f}")
    print()
    print(f"  summary: {result.summary}")


def _cmd_run_cycle(args: argparse.Namespace) -> None:
    import uuid as _uuid

    from .coordinator import run_cycle
    from .interactive import run_with_watcher

    cycle_id = f"cycle_{_uuid.uuid4().hex[:10]}"

    print(f"[cycle] {args.asset} | {args.horizon} | id={cycle_id}")
    if args.interactive:
        print("[mode] interactive — gates will block at the terminal")
    else:
        print("[mode] auto-approve — gates auto-approve immediately")

    async def _run():
        return await run_cycle(
            args.asset,
            args.horizon,
            auto_approve_gates=not args.interactive,
            cycle_id=cycle_id,
        )

    if args.interactive:
        state = asyncio.run(run_with_watcher(_run, cycle_id))
    else:
        state = asyncio.run(_run())

    print()
    print(f"[done] cycle_id={state.cycle_id} completed={state.completed}")
    print(f"  cycle dir: {state.dir()}")
    if state.completed:
        print(f"  report:    {state.final_report_path_md()}")


def _cmd_list_cycles(args: argparse.Namespace) -> None:
    from .cycle import list_cycles

    cycles = list_cycles()
    if not cycles:
        print("(no cycles yet)")
        return
    for c in cycles:
        gates = " ".join(f"{k}={v.status}" for k, v in c.gates.items())
        print(
            f"{c.cycle_id}  {c.asset!r:20s}  {c.horizon!r:14s}  "
            f"completed={c.completed}  {gates}"
        )


def _cmd_render_site(args: argparse.Namespace) -> None:
    from .render_site import render_cycle_to_site

    site = render_cycle_to_site(args.cycle_id)
    print(f"[site] rendered → {site}")
    print(f"  open: file://{(site / 'index.html').resolve()}")
    print(f"  deploy: drag the {site} folder to Vercel/Netlify")


def main() -> None:
    parser = argparse.ArgumentParser(prog="lean-alpha")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("run-specialist", help="Run a single specialist (M1 test)")
    sp.add_argument("name", help="Specialist name, e.g. A2.1")
    sp.add_argument("brief", help="Brief, e.g. 'Archer Aviation, 12 months'")
    sp.add_argument("--cycle-dir", type=Path, default=None)
    sp.add_argument("--prior", action="append", default=[])
    sp.add_argument("--feedback", default=None)
    sp.add_argument("--iteration", type=int, default=0)
    sp.set_defaults(func=_cmd_run_specialist)

    rc = sub.add_parser("run-cycle", help="Run the full A1-coordinated cycle")
    rc.add_argument("asset", help="Asset / theme")
    rc.add_argument("horizon", help="Horizon, e.g. '12 months'")
    rc.add_argument(
        "--interactive",
        action="store_true",
        help="Block at gates and prompt the analyst at the terminal "
        "(default: auto-approve every gate)",
    )
    rc.set_defaults(func=_cmd_run_cycle)

    lc = sub.add_parser("list-cycles", help="List cycles on disk")
    lc.set_defaults(func=_cmd_list_cycles)

    rs = sub.add_parser(
        "render-site",
        help="Render a cycle to a self-contained static HTML site (drag to "
        "Vercel/Netlify)",
    )
    rs.add_argument("cycle_id", help="Cycle ID, e.g. cycle_4e49685c12")
    rs.set_defaults(func=_cmd_render_site)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

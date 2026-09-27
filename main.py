from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "arcbench-agent-runtime" / "src"))

from arcbench_agent_runtime import AgentRuntime  # noqa: E402

from arc_agent.config import Config  # noqa: E402
from arc_agent.orchestrator import Agent  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a web application from ARC-Bench requirements.")
    parser.add_argument("requirement_path", nargs="?", default=os.environ.get("ARCBENCH_TASK_DIR", "requirements"))
    parser.add_argument("--output-dir", default=os.environ.get("ARCBENCH_OUTPUT_DIR", "."))
    parser.add_argument("--type", dest="task_type", default=os.environ.get("ARCBENCH_TASK_TYPE", "web"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime = AgentRuntime.from_env(project_dir=args.output_dir)
    config = Config.from_env(Path(args.requirement_path), Path(args.output_dir), args.task_type)
    try:
        runtime.events.mark_run_started("Requirement-driven application generation started")
        Agent(config, runtime).run()
        runtime.events.mark_run_completed("Application generation and local validation completed")
        return 0
    except Exception as exc:
        runtime.events.mark_run_failed(f"{type(exc).__name__}: {str(exc)[:500]}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())

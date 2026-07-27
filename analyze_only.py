from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import time

import main


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run only the production primary-analyzer custom role and publish "
            "primary-analysis.v1."
        )
    )
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.perf_counter()
    workspace: Path | None = None
    completed = False
    try:
        workspace, output = main.prepare_workspace(
            args.contract,
            args.matrix,
            args.output,
        )
        main.run_agent(
            workspace,
            run_prompt=main.PRIMARY_ONLY_RUN_PROMPT,
            system_prompt=main.PRIMARY_ONLY_ORCHESTRATOR_SYSTEM_PROMPT,
        )
        generated = workspace / main.PRIMARY_ARTIFACT
        main.validate_primary_artifact(generated)
        shutil.copyfile(generated, output)
        completed = True
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if workspace is not None:
            if completed:
                shutil.rmtree(workspace, ignore_errors=True)
            else:
                print(
                    f"[workspace] preserved after failure: {workspace}",
                    file=sys.stderr,
                    flush=True,
                )

    print(f"[total] finished in {time.perf_counter() - started:.1f}s", flush=True)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())

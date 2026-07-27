from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid

import httpx
import openai
from deepagents import create_deep_agent

import main
from llm import get_llm


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run only the production final-selector custom role against a "
            "complete-analysis.v1 artifact."
        )
    )
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def prepare_workspace(analysis: Path, output: Path) -> tuple[Path, Path]:
    analysis = analysis.expanduser().resolve()
    main.validate_complete_artifact(analysis)
    output = main._resolve_json_output(output, "Output")
    if output == analysis:
        raise ValueError("Output path must differ from analysis path")

    workspace = Path(tempfile.mkdtemp(prefix="contract-review-selector-"))
    (workspace / "outputs" / "working").mkdir(parents=True)
    shutil.copyfile(analysis, workspace / main.COMPLETE_ARTIFACT)
    shutil.copytree(
        main.SELECTION_SKILL_SOURCE,
        workspace / "skills" / main.SELECTION_SKILL_SOURCE.name,
    )
    return workspace, output


def run_selector(workspace: Path) -> None:
    selector_definition = main._subagent_definitions()[2]
    agent = create_deep_agent(
        name="contract-review-selector-orchestrator",
        model=get_llm(),
        system_prompt=main.SELECTION_ONLY_ORCHESTRATOR_SYSTEM_PROMPT,
        backend=main.build_backend(workspace),
        skills=[],
        subagents=[selector_definition],
    )
    retryable_errors = (
        httpx.TransportError,
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.InternalServerError,
        openai.RateLimitError,
    )
    retry_number = 0
    thread_id = uuid.uuid4().hex
    while True:
        try:
            agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": main.SELECTION_ONLY_RUN_PROMPT,
                        }
                    ]
                },
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": 10_000,
                    "callbacks": [main.CompactTraceHandler()],
                },
            )
            return
        except retryable_errors as exc:
            retry_number += 1
            delay_seconds = min(2**retry_number, 30)
            print(
                f"Transient failure ({type(exc).__name__}); retrying in "
                f"{delay_seconds}s [retry {retry_number}]",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay_seconds)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.perf_counter()
    workspace: Path | None = None
    completed = False
    try:
        workspace, output = prepare_workspace(args.analysis, args.output)
        run_selector(workspace)
        generated = workspace / main.FINAL_ARTIFACT
        main.validate_conclusion_artifact(generated)
        shutil.copyfile(generated, output)
        completed = True
    except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
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
